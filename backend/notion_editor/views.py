from rest_framework import viewsets, status, permissions
from core.slug_mixins import SlugLookupViewSetMixin, resolve_lookup_kwargs
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
import rest_framework.parsers
import requests
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.http import JsonResponse
from django.contrib.auth import get_user_model
from django.db.models import Max
from django.utils import timezone
import os
import tempfile
import logging
from django.core.files.uploadedfile import InMemoryUploadedFile, TemporaryUploadedFile, SimpleUploadedFile
from django.core.files.base import ContentFile
from utils.virus_scanner import perform_clamav_scan
from core.services.oauth_state import OAuthStateExpired, OAuthStateInvalid, create_oauth_state, validate_oauth_state
from .models import Draft, ContentBlock, BlockAction, DraftRevision, MediaFile, NotionConnection
from .serializers import (
    DraftSerializer, DraftListSerializer, CreateDraftSerializer, UpdateDraftSerializer,
    ContentBlockSerializer, BlockActionSerializer, BlockActionCreateSerializer,
    DraftRevisionSerializer, DraftRevisionListSerializer,
    MediaFileSerializer, MediaFileUploadSerializer,
    NotionStatusSerializer, NotionConnectSerializer, NotionDisconnectSerializer,
    NotionImportSerializer, NotionExportSerializer,
)
from .services import (
    build_notion_auth_url,
    exchange_notion_code_for_token,
    export_draft_to_notion,
    fetch_notion_bot_name,
    import_notion_page_as_draft,
)

User = get_user_model()
logger = logging.getLogger(__name__)
NOTION_STATE_SALT = "notion-oauth-state"
NOTION_STATE_MAX_AGE_SECONDS = 600


def _build_notion_oauth_state(user) -> str:
    return create_oauth_state(
        flow=NOTION_STATE_SALT,
        payload={"user_id": user.id},
        ttl_seconds=NOTION_STATE_MAX_AGE_SECONDS,
    )


def _notion_api_error_response(exc: requests.HTTPError) -> Response:
    status_code = exc.response.status_code if exc.response is not None else None
    if status_code == 401:
        return Response(
            {"error": "Notion session expired. Please reconnect Notion in Integrations."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if status_code == 403:
        return Response(
            {"error": "Notion denied access. Share the page with your integration and try again."},
            status=status.HTTP_403_FORBIDDEN,
        )
    if status_code == 404:
        return Response(
            {"error": "Notion page not found. Check the page ID and sharing settings."},
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response(
        {"error": "Notion request failed. Please try again in a moment."},
        status=status.HTTP_502_BAD_GATEWAY,
    )


def _validation_error_response(exc: DjangoValidationError) -> Response:
    message = exc.messages[0] if hasattr(exc, "messages") else str(exc)
    return Response({"error": message}, status=status.HTTP_400_BAD_REQUEST)


def _draft_summary(draft: Draft) -> dict:
    return {
        "id": draft.id,
        "title": draft.title,
        "status": draft.status,
        "content_blocks_count": draft.get_content_blocks_count(),
        "updated_at": draft.updated_at,
    }


class NotionStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        connection = NotionConnection.objects.filter(user=request.user, is_active=True).first()
        has_token = bool(connection and connection.get_access_token())
        payload = {
            "connected": has_token,
            "workspace_id": connection.workspace_id if connection else None,
            "workspace_name": connection.workspace_name if connection else None,
            "workspace_icon": connection.workspace_icon if connection else None,
            "bot_id": connection.bot_id if connection else None,
            "bot_name": connection.bot_name if connection else None,
            "connected_at": connection.connected_at if connection and has_token else None,
        }
        serializer = NotionStatusSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)


class NotionConnectView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        missing_settings = []
        if not settings.NOTION_OAUTH_CLIENT_ID:
            missing_settings.append("NOTION_CLIENT_ID")
        if not settings.NOTION_OAUTH_CLIENT_SECRET:
            missing_settings.append("NOTION_CLIENT_SECRET")
        if not settings.NOTION_OAUTH_REDIRECT_URI:
            missing_settings.append("NOTION_OAUTH_REDIRECT_URI")

        if missing_settings:
            return Response(
                {
                    "error": "Notion OAuth is not configured.",
                    "details": {"missing_settings": missing_settings},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        state = _build_notion_oauth_state(request.user)
        serializer = NotionConnectSerializer(data={"auth_url": build_notion_auth_url(state), "state": state})
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)


class NotionCallbackView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        oauth_error = request.query_params.get("error")

        if oauth_error:
            return redirect(f"{settings.FRONTEND_URL}/integrations?notion_error=access_denied")
        if not code or not state:
            return redirect(f"{settings.FRONTEND_URL}/integrations?notion_error=missing_code")

        try:
            payload = validate_oauth_state(
                state,
                expected_flow=NOTION_STATE_SALT,
                ttl_seconds=NOTION_STATE_MAX_AGE_SECONDS,
            )
        except OAuthStateExpired:
            return redirect(f"{settings.FRONTEND_URL}/integrations?notion_error=state_expired")
        except OAuthStateInvalid:
            return redirect(f"{settings.FRONTEND_URL}/integrations?notion_error=invalid_state")

        user_id = payload.get("user_id")
        if not user_id:
            return redirect(f"{settings.FRONTEND_URL}/integrations?notion_error=invalid_state")

        try:
            token_data = exchange_notion_code_for_token(code)
            access_token = token_data.get("access_token")
            if not access_token:
                raise ValueError("No access token returned by Notion.")

            connection, _ = NotionConnection.objects.get_or_create(user_id=user_id)
            connection.workspace_id = token_data.get("workspace_id")
            connection.workspace_name = token_data.get("workspace_name")
            connection.workspace_icon = token_data.get("workspace_icon")
            owner_data = token_data.get("owner", {}) if isinstance(token_data.get("owner"), dict) else {}
            owner_user = owner_data.get("user", {}) if isinstance(owner_data.get("user"), dict) else {}
            connection.bot_id = owner_user.get("id")
            connection.bot_name = fetch_notion_bot_name(access_token)
            connection.set_access_token(access_token)
            connection.is_active = True
            connection.connected_at = timezone.now()
            connection.save()
        except Exception:
            logger.exception("Failed to connect Notion integration")
            return redirect(f"{settings.FRONTEND_URL}/integrations?notion_error=token_exchange_failed")

        return redirect(f"{settings.FRONTEND_URL}/integrations?open_notion=1")


class NotionDisconnectView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        connection = NotionConnection.objects.filter(user=request.user, is_active=True).first()
        if connection:
            connection.disconnect()
        serializer = NotionDisconnectSerializer(data={"success": True})
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)


class NotionImportView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = NotionImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            draft, source_page_id = import_notion_page_as_draft(
                user=request.user,
                page_ref=serializer.validated_data["page"],
                draft_id=serializer.validated_data.get("draft_id"),
            )
        except DjangoValidationError as exc:
            return _validation_error_response(exc)
        except requests.HTTPError as exc:
            return _notion_api_error_response(exc)
        except requests.RequestException:
            return Response(
                {"error": "Notion service is temporarily unavailable. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                "source_page_id": source_page_id,
                "draft": _draft_summary(draft),
            },
            status=status.HTTP_200_OK,
        )


class NotionExportView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = NotionExportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            payload = export_draft_to_notion(
                user=request.user,
                draft_id=serializer.validated_data["draft_id"],
                parent_page_id=serializer.validated_data.get("parent_page_id"),
                title=serializer.validated_data.get("title"),
            )
        except DjangoValidationError as exc:
            return _validation_error_response(exc)
        except requests.HTTPError as exc:
            return _notion_api_error_response(exc)
        except requests.RequestException:
            return Response(
                {"error": "Notion service is temporarily unavailable. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(payload, status=status.HTTP_200_OK)


class DraftViewSet(SlugLookupViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing drafts
    """
    queryset = Draft.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """Return drafts for the current user"""
        return Draft.objects.filter(
            user=self.request.user, 
            is_deleted=False
        ).select_related('user').prefetch_related('blocks__actions')
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'list':
            return DraftListSerializer
        elif self.action == 'create':
            return CreateDraftSerializer
        elif self.action in ['update', 'partial_update']:
            return UpdateDraftSerializer
        return DraftSerializer
    
    def perform_create(self, serializer):
        """Set the user when creating a draft and create initial revision"""
        draft = serializer.save(user=self.request.user)
        # Create initial revision
        self._create_revision(draft, "Initial version")

    def perform_update(self, serializer):
        """Update draft and create a new revision"""
        draft = serializer.save()
        # Create revision snapshot
        change_summary = self.request.data.get('change_summary', 'Updated draft')
        self._create_revision(draft, change_summary)

    def perform_destroy(self, instance):
        """Soft delete the draft"""
        instance.is_deleted = True
        instance.save()

    def _create_revision(self, draft, change_summary=""):
        """Helper method to create a revision snapshot"""
        # Get the next revision number
        last_revision = draft.revisions.aggregate(Max('revision_number'))['revision_number__max']
        next_revision_number = (last_revision or 0) + 1

        # Create the revision
        DraftRevision.objects.create(
            draft=draft,
            title=draft.title,
            content_blocks=draft.content_blocks.copy() if isinstance(draft.content_blocks, list) else [],
            status=draft.status,
            revision_number=next_revision_number,
            change_summary=change_summary,
            created_by=self.request.user
        )
    
    @action(detail=True, methods=['post'])
    def add_block(self, request, pk=None):
        """Add a new content block to the draft"""
        draft = self.get_object()
        block_data = request.data
        
        if not isinstance(block_data, dict):
            return Response(
                {'error': 'Block data must be a dictionary'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Add block to JSON content_blocks
        block_id = draft.add_content_block(block_data)
        
        # Also create a ContentBlock record for structured storage
        content_block = ContentBlock.objects.create(
            draft=draft,
            block_type=block_data.get('type', 'text'),
            content=block_data.get('content', {}),
            order=len(draft.content_blocks) - 1
        )
        
        return Response({
            'block_id': block_id,
            'content_block_id': content_block.id,
            'message': 'Block added successfully'
        })
    
    @action(detail=True, methods=['put'])
    def update_block(self, request, pk=None):
        """Update a content block in the draft"""
        draft = self.get_object()
        block_id = request.data.get('block_id')
        block_data = request.data.get('block_data')
        
        if not block_id or not block_data:
            return Response(
                {'error': 'block_id and block_data are required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        success = draft.update_content_block(block_id, block_data)
        
        if success:
            return Response({'message': 'Block updated successfully'})
        else:
            return Response(
                {'error': 'Block not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
    
    @action(detail=True, methods=['delete'])
    def delete_block(self, request, pk=None):
        """Delete a content block from the draft"""
        draft = self.get_object()
        block_id = request.data.get('block_id')
        
        if not block_id:
            return Response(
                {'error': 'block_id is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        success = draft.delete_content_block(block_id)
        
        if success:
            return Response({'message': 'Block deleted successfully'})
        else:
            return Response(
                {'error': 'Block not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
    


class ContentBlockViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing content blocks
    """
    queryset = ContentBlock.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """Return content blocks for the current user's drafts"""
        return ContentBlock.objects.filter(
            draft__user=self.request.user,
            draft__is_deleted=False
        ).select_related('draft').prefetch_related('actions')
    
    def get_serializer_class(self):
        return ContentBlockSerializer
    
    def perform_create(self, serializer):
        """Ensure the draft belongs to the current user"""
        draft = serializer.validated_data['draft']
        if draft.user != self.request.user:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You can only create blocks for your own drafts")
        serializer.save()


class BlockActionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing block actions
    """
    queryset = BlockAction.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """Return block actions for the current user's drafts"""
        return BlockAction.objects.filter(
            block__draft__user=self.request.user,
            block__draft__is_deleted=False
        ).select_related('block__draft')
    
    def get_serializer_class(self):
        if self.action == 'create':
            return BlockActionCreateSerializer
        return BlockActionSerializer


class DraftBlocksView(APIView):
    """
    API view for managing blocks of a specific draft
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, draft_id):
        """Get all blocks for a draft"""
        draft = get_object_or_404(
            Draft, 
            **resolve_lookup_kwargs(draft_id, 'id'), 
            user=request.user, 
            is_deleted=False
        )
        
        blocks = draft.blocks.all().order_by('order', 'created_at')
        serializer = ContentBlockSerializer(blocks, many=True)
        
        return Response({
            'draft_id': draft.id,
            'draft_title': draft.title,
            'blocks': serializer.data
        })


class ExportDraftView(APIView):
    """
    API view for exporting a draft
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, draft_id):
        """Export draft as downloadable JSON"""
        draft = get_object_or_404(
            Draft, 
            **resolve_lookup_kwargs(draft_id, 'id'), 
            user=request.user, 
            is_deleted=False
        )
        
        export_data = {
            'title': draft.title,
            'status': draft.status,
            'content_blocks': draft.content_blocks,
            'created_at': draft.created_at.isoformat(),
            'updated_at': draft.updated_at.isoformat(),
            'exported_at': draft.updated_at.isoformat(),
        }
        
        response = JsonResponse(export_data, json_dumps_params={'indent': 2})
        response['Content-Disposition'] = f'attachment; filename="{draft.title}_export.json"'
        return response


class DuplicateDraftView(APIView):
    """
    API view for duplicating a draft
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request, draft_id):
        """Duplicate a draft"""
        original_draft = get_object_or_404(
            Draft, 
            **resolve_lookup_kwargs(draft_id, 'id'), 
            user=request.user, 
            is_deleted=False
        )
        
        new_title = request.data.get('title', f"{original_draft.title} (Copy)")
        
        new_draft = Draft.objects.create(
            title=new_title,
            user=request.user,
            status='draft',
            content_blocks=original_draft.content_blocks.copy()
        )
        
        # Duplicate content blocks
        for block in original_draft.blocks.all():
            new_block = ContentBlock.objects.create(
                draft=new_draft,
                block_type=block.block_type,
                content=block.content,
                order=block.order
            )
            
            # Duplicate block actions
            for action in block.actions.all():
                BlockAction.objects.create(
                    block=new_block,
                    action_type=action.action_type,
                    label=action.label,
                    icon=action.icon,
                    is_enabled=action.is_enabled,
                    order=action.order
                )
        
        serializer = DraftSerializer(new_draft)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class DraftRevisionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing draft revisions (read-only)
    """
    queryset = DraftRevision.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Return revisions for the current user's drafts"""
        return DraftRevision.objects.filter(
            draft__user=self.request.user,
            draft__is_deleted=False
        ).select_related('draft', 'created_by')

    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'list':
            return DraftRevisionListSerializer
        return DraftRevisionSerializer

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        """Restore a draft to a specific revision"""
        revision = self.get_object()
        draft = revision.draft

        # Verify ownership
        if draft.user != request.user:
            return Response(
                {'error': 'You can only restore your own drafts'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Restore the draft to this revision's state
        draft.title = revision.title
        draft.content_blocks = revision.content_blocks.copy() if isinstance(revision.content_blocks, list) else []
        draft.status = revision.status
        draft.save()

        # Create a new revision marking the restoration
        last_revision = draft.revisions.aggregate(Max('revision_number'))['revision_number__max']
        next_revision_number = (last_revision or 0) + 1

        DraftRevision.objects.create(
            draft=draft,
            title=draft.title,
            content_blocks=draft.content_blocks.copy(),
            status=draft.status,
            revision_number=next_revision_number,
            change_summary=f"Restored to revision {revision.revision_number}",
            created_by=request.user
        )

        serializer = DraftSerializer(draft)
        return Response({
            'message': f'Draft restored to revision {revision.revision_number}',
            'draft': serializer.data
        })


class DraftRevisionsListView(APIView):
    """
    API view for listing all revisions of a specific draft
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, draft_id):
        """Get all revisions for a draft"""
        draft = get_object_or_404(
            Draft,
            id=draft_id,
            user=request.user,
            is_deleted=False
        )

        revisions = draft.revisions.all()
        serializer = DraftRevisionListSerializer(revisions, many=True)

        return Response({
            'draft_id': draft.id,
            'draft_title': draft.title,
            'total_revisions': revisions.count(),
            'revisions': serializer.data
        })


class MediaFileViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing media files
    """
    queryset = MediaFile.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """Return media files for the current user"""
        return MediaFile.objects.filter(
            uploaded_by=self.request.user
        ).select_related('uploaded_by', 'draft')
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'create':
            return MediaFileUploadSerializer
        return MediaFileSerializer
    
    def perform_create(self, serializer):
        """Set the user when creating a media file"""
        serializer.save(uploaded_by=self.request.user)


class MediaUploadView(APIView):
    """
    API view for uploading media files (image, video, audio, file)
    Performs virus scan before saving the file
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [rest_framework.parsers.MultiPartParser, rest_framework.parsers.FormParser]
    
    def post(self, request):
        """Upload a media file with virus scanning"""
        file_obj = request.FILES.get('file')
        media_type = request.data.get('media_type')
        draft_id = request.data.get('draft_id')
        
        if not file_obj:
            return Response(
                {'error': 'File is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        logger.info(f"Media upload request: filename={file_obj.name}, size={file_obj.size}, media_type={media_type}")
        
        # Get draft if provided
        draft = None
        if draft_id:
            try:
                draft = Draft.objects.get(**resolve_lookup_kwargs(draft_id, 'id'), user=request.user, is_deleted=False)
            except Draft.DoesNotExist:
                return Response(
                    {'error': 'Draft not found or access denied'}, 
                    status=status.HTTP_404_NOT_FOUND
                )
        
        # Save uploaded file to temporary location for virus scanning
        temp_file_path = None
        file_content = None
        try:
            # Read file content into memory first
            file_obj.seek(0)
            file_content = file_obj.read()
            file_obj.seek(0)  # Reset for later use
            
            # Create temporary file for virus scanning
            file_ext = os.path.splitext(file_obj.name)[1] if hasattr(file_obj, 'name') else ''
            temp_fd, temp_file_path = tempfile.mkstemp(prefix='notion_upload_', suffix=file_ext)
            
            # Write file content to temporary location
            try:
                with os.fdopen(temp_fd, 'wb') as temp_file:
                    temp_file.write(file_content)
            except Exception as e:
                logger.error(f"Failed to write to temp file: {str(e)}")
                os.unlink(temp_file_path)
                return Response(
                    {'error': f'Failed to process file: {str(e)}'}, 
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            # Perform virus scan before saving
            try:
                logger.info(f"Performing virus scan on: {temp_file_path}")
                is_infected = perform_clamav_scan(temp_file_path)
                if is_infected:
                    logger.warning(f"File failed virus scan: {file_obj.name}")
                    # Delete temporary file
                    if os.path.exists(temp_file_path):
                        os.unlink(temp_file_path)
                    return Response(
                        {'error': 'File failed virus scan. Upload rejected for security reasons.'}, 
                        status=status.HTTP_403_FORBIDDEN
                    )
                logger.info(f"Virus scan passed: {file_obj.name}")
            except RuntimeError as e:
                logger.error(f"Virus scanner error: {str(e)}")
                # Scanner error - delete temporary file
                if os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)
                return Response(
                    {'error': f'Virus scanner error: {str(e)}'}, 
                    status=status.HTTP_503_SERVICE_UNAVAILABLE
                )
            except Exception as e:
                logger.error(f"Unexpected error during virus scan: {str(e)}")
                # Unexpected error - delete temporary file
                if os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)
                return Response(
                    {'error': f'Virus scan failed: {str(e)}'}, 
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            # File is clean, now save it properly
            # Create a new file object from the content for Django FileField
            # Use SimpleUploadedFile which is compatible with FileField
            original_filename = file_obj.name if hasattr(file_obj, 'name') else 'upload'
            original_content_type = file_obj.content_type if hasattr(file_obj, 'content_type') else None
            
            # Create new SimpleUploadedFile from the file content we already read
            file_obj = SimpleUploadedFile(
                name=original_filename,
                content=file_content,
                content_type=original_content_type
            )
            
            # Create serializer
            serializer_data = {
                'file': file_obj,
                'media_type': media_type,
            }
            if draft:
                serializer_data['draft'] = draft.id
            
            serializer = MediaFileUploadSerializer(
                data=serializer_data,
                context={'request': request}
            )
            
            if serializer.is_valid():
                try:
                    # Set scan_status to READY since we already scanned
                    serializer.validated_data['scan_status'] = MediaFile.READY
                    media_file = serializer.save()
                    logger.info(f"Media file created successfully: id={media_file.id}, filename={media_file.original_filename}")
                    
                    # Clean up temporary file after successful save
                    if temp_file_path and os.path.exists(temp_file_path):
                        try:
                            os.unlink(temp_file_path)
                        except Exception as e:
                            logger.warning(f"Failed to delete temp file {temp_file_path}: {str(e)}")
                    
                    # Return the media file data with block structure
                    return Response({
                        'id': media_file.id,
                        'file_url': request.build_absolute_uri(media_file.file.url),
                        'media_type': media_file.media_type,
                        'original_filename': media_file.original_filename,
                        'file_size': media_file.file_size,
                        'content_type': media_file.content_type,
                        'scan_status': media_file.scan_status,
                        'block_data': {
                            'type': media_file.media_type,
                            'content': {
                                'file_id': media_file.id,
                                'file_url': request.build_absolute_uri(media_file.file.url),
                                'filename': media_file.original_filename,
                                'file_size': media_file.file_size,
                                'content_type': media_file.content_type,
                            }
                        }
                    }, status=status.HTTP_201_CREATED)
                except Exception as e:
                    logger.error(f"Failed to save media file: {str(e)}", exc_info=True)
                    # Clean up temp file if save fails
                    if temp_file_path and os.path.exists(temp_file_path):
                        os.unlink(temp_file_path)
                    return Response(
                        {'error': f'Failed to save file: {str(e)}'}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
            
            # Clean up temp file if serializer fails
            logger.warning(f"Serializer validation failed: {serializer.errors}")
            if temp_file_path and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        except Exception as e:
            logger.error(f"Unexpected error in media upload: {str(e)}", exc_info=True)
            # Clean up temporary file on any error
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except OSError:
                    pass  # Ignore cleanup errors
            return Response(
                {'error': f'File upload error: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WebBookmarkView(APIView):
    """
    API view for creating web bookmark blocks
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request):
        """Create a web bookmark block"""
        url = request.data.get('url')
        draft_id = request.data.get('draft_id')
        
        if not url:
            return Response(
                {'error': 'URL is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get draft if provided
        draft = None
        if draft_id:
            try:
                draft = Draft.objects.get(**resolve_lookup_kwargs(draft_id, 'id'), user=request.user, is_deleted=False)
            except Draft.DoesNotExist:
                return Response(
                    {'error': 'Draft not found or access denied'}, 
                    status=status.HTTP_404_NOT_FOUND
                )
        
        # Create block data structure
        block_data = {
            'type': 'web_bookmark',
            'content': {
                'url': url,
                'title': request.data.get('title', ''),
                'description': request.data.get('description', ''),
                'favicon': request.data.get('favicon', ''),
            }
        }
        
        # If draft is provided, add the block to the draft
        if draft:
            block_id = draft.add_content_block(block_data)
            
            # Also create a ContentBlock record
            content_block = ContentBlock.objects.create(
                draft=draft,
                block_type='web_bookmark',
                content=block_data['content'],
                order=len(draft.content_blocks) - 1
            )
            
            return Response({
                'block_id': block_id,
                'content_block_id': content_block.id,
                'block_data': block_data,
                'message': 'Web bookmark block created successfully'
            }, status=status.HTTP_201_CREATED)
        
        # Return block data without adding to draft
        return Response({
            'block_data': block_data,
            'message': 'Web bookmark block data created'
        }, status=status.HTTP_201_CREATED)

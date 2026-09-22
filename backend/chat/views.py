import logging
import base64
import json
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from rest_framework import mixins, viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.throttling import ScopedRateThrottle
from django.db import transaction
from django.db.models import Q, Prefetch
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.core.cache import cache
from django.http import HttpResponse
from django.conf import settings
from datetime import datetime
from .models import Chat, ChatParticipant, LinkPreview, Message, MessageStatus, ChatType, ChannelVisibility, MessageAttachment, MessageReaction, PinnedMessage, SavedMessage, ScheduledMessage
from .serializers import (
    ChatSerializer,
    ChatListSerializer,
    ChatStarSerializer,
    ChatStarCreateSerializer,
    ChatStarReorderSerializer,
    ChatCreateSerializer,
    ChatUpdateSerializer,
    PinnedMessageSerializer,
    PinMessageRequestSerializer,
    ParticipantNotificationSerializer,
    SavedMessageSerializer,
    MessageSerializer,
    MessageCreateSerializer,
    MessageWithAttachmentsSerializer,
    MessageCreateWithAttachmentsSerializer,
    ChatParticipantSerializer,
    MarkAsReadSerializer,
    ForwardBatchSerializer,
    MessageAttachmentSerializer,
    AttachmentUploadSerializer,
    AttachmentFileListRowSerializer,
    AddReactionSerializer,
    ScheduledMessageSerializer,
    ScheduledMessageCreateSerializer,
)
from .services import (
    ChatService,
    ChatStarService,
    LinkPreviewFetchError,
    MessageService,
    OnlineStatusService,
    UnsafeUrlError,
    UnsupportedAttachmentMimeType,
    fetch_image_safely,
    fetch_url_safely,
    validate_attachment_mime_type,
    validate_public_url,
)
from .metrics import chat_broadcast_enqueue_failures_total, chat_link_preview_refusals_total
from .tasks import notify_pin_update, send_scheduled_message
from core.models import ProjectMember
from core.slug_mixins import resolve_project_pk, SlugLookupViewSetMixin
from core.tenant_context import current_tenant_schema

logger = logging.getLogger(__name__)


def queue_pin_update(chat_id, action, message_id, pin_data=None, actor_user_id=None):
    """Queue a shared pin change for broadcast to a channel's other members.

    Fan-out runs on the dedicated realtime worker rather than inside the
    request: a large channel would otherwise cost one sequential Channels
    publication per member before the caller gets a response.

    Shared by the pin/unpin actions and by the delete/revoke paths, which drop
    a message's pin row as a side effect. Those live on a different viewset,
    and leaving them out is what let members keep a pin the server had already
    removed until they reloaded.
    """
    chat_id = int(chat_id)
    tenant_schema = current_tenant_schema()

    def enqueue() -> None:
        try:
            notify_pin_update.delay(
                chat_id,
                action,
                message_id,
                pin_data,
                tenant_schema=tenant_schema,
                actor_user_id=actor_user_id,
            )
        except Exception:
            # A broker failure must never roll back a pin change that has
            # already been persisted successfully. The pin state is durable
            # either way; what is lost is the live update, so members only see
            # it after a refresh. Counted as well as logged: this is silent
            # from the user's side and nobody reads logs looking for it.
            chat_broadcast_enqueue_failures_total.labels(event='pin').inc()
            logger.exception('Failed to queue pin update for chat %s', chat_id)

    transaction.on_commit(enqueue)


def _encode_search_cursor(message, include_rank=False):
    payload = {
        'created_at': message.created_at.isoformat(),
        'id': message.id,
    }
    if include_rank:
        payload['rank'] = float(getattr(message, 'rank', 0) or 0)
    raw = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _decode_search_cursor(cursor):
    try:
        padded = cursor + ('=' * (-len(cursor) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode('ascii')).decode('utf-8'))
        created_at = datetime.fromisoformat(str(payload['created_at']).replace('Z', '+00:00'))
        message_id = int(payload['id'])
        rank = payload.get('rank')
        return {
            'created_at': created_at,
            'id': message_id,
            'rank': float(rank) if rank is not None else None,
        }
    except Exception:
        return None


class StarredChatViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Starred chats for the current user.

    - GET /starred/?project_id= - list starred chats in project (ordered)
    - POST /starred/ body { chat_id } - star a chat
    - DELETE /starred/{chat_id}/ - unstar (pk is chat id, not ChatStar row id)
    - POST /starred/reorder/ body { project_id, chat_ids } - reorder
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ChatStarSerializer

    def get_queryset(self):
        return self.request.user.chat_stars.none()

    def list(self, request, *args, **kwargs):
        project_id = request.query_params.get('project_id')
        if not project_id:
            return Response(
                {'error': 'project_id is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        pid = resolve_project_pk(project_id)
        if pid is None:
            return Response(
                {'error': 'Invalid project_id'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        stars = ChatStarService.list_starred_for_project(request.user, pid)
        serializer = ChatStarSerializer(stars, many=True, context={'request': request})
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        serializer = ChatStarCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            star, created = ChatStarService.star_chat(
                request.user, serializer.validated_data['chat_id']
            )
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        out = ChatStarSerializer(star, context={'request': request})
        return Response(
            out.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def destroy(self, request, *args, **kwargs):
        try:
            chat_id = int(kwargs.get('pk'))
        except (TypeError, ValueError):
            return Response({'error': 'Invalid chat id'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            ChatStarService.unstar_chat(request.user, chat_id)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['post'], url_path='reorder')
    def reorder(self, request):
        serializer = ChatStarReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            ChatStarService.reorder_starred(
                request.user,
                serializer.validated_data['project_id'],
                serializer.validated_data['chat_ids'],
            )
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'status': 'ok'})


class ChatViewSet(SlugLookupViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing chats.
    
    Endpoints:
    - GET /chats/ - List user's chats
    - POST /chats/ - Create a new chat
    - GET /chats/{slug}/ - Get chat details
    - DELETE /chats/{slug}/ - Leave a chat (soft delete for user)
    - POST /chats/{slug}/add_participant/ - Add participant to group chat
    - POST /chats/{slug}/remove_participant/ - Remove participant from group chat
    - POST /chats/{slug}/mark_as_read/ - Mark all messages as read
    - GET /chats/legacy-id-slug/?id=<pk> - Resolve legacy numeric id to slug (migration)
    """
    
    permission_classes = [IsAuthenticated]
    lookup_field = 'slug'

    def _get_active_participant(self, chat, user):
        return ChatParticipant.objects.filter(chat=chat, user=user, is_active=True).first()

    def _get_fallback_manager_user_id(self, chat):
        """Legacy channels may predate managers; treat the first active member as manager."""
        return ChatService.get_fallback_manager_user_id(chat)

    def _is_channel_manager(self, chat, user):
        return ChatService.is_channel_manager(chat, user)

    def _notify_pin_update(self, chat, action, message_id, pin_data=None):
        queue_pin_update(
            chat.id,
            action,
            message_id,
            pin_data=pin_data,
            actor_user_id=self.request.user.id,
        )

    def get_queryset(self):
        """Get chats where user is a participant"""
        # For actions where the user may not be a member yet, return all chats.
        # Actual permission / membership checks happen inside those views.
        if self.action in ('retrieve', 'add_participant', 'browse'):
            return Chat.objects.all()

        # For list and other actions, filter by user participation
        user = self.request.user
        project_id = (
            self.request.query_params.get('project_id')
            or self.request.query_params.get('pro_ct_id')
        )
        if project_id:
            project_id = resolve_project_pk(project_id)

        return ChatService.get_user_chats(user, project_id)
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'list':
            return ChatListSerializer
        elif self.action == 'create':
            return ChatCreateSerializer
        return ChatSerializer
    
    def list(self, request, *args, **kwargs):
        """
        List user's chats with pagination.
        
        Query params:
        - project_id: Filter by project (optional)
        - type: Filter by chat type ('private' or 'group', optional)
        - page: Page number (default: 1)
        - page_size: Items per page (default: 20)
        - limit: Alternative to page_size (for compatibility)
        """
        logger.info(f"User {request.user.id} listing chats")
        
        queryset = self.get_queryset()
        
        # Filter by chat type if provided
        chat_type = request.query_params.get('type')
        if chat_type:
            queryset = queryset.filter(type=chat_type)
        
        # Pagination
        page = int(request.query_params.get('page', 1))
        # Support both 'page_size' and 'limit' parameters
        page_size = int(request.query_params.get('page_size', request.query_params.get('limit', 20)))
        
        start = (page - 1) * page_size
        end = start + page_size
        
        chats = queryset[start:end]
        serializer = self.get_serializer(chats, many=True)
        
        return Response({
            'results': serializer.data,
            'page': page,
            'page_size': page_size,
            'total': queryset.count()
        })

    @action(detail=False, methods=['get'], url_path='legacy-id-slug')
    def legacy_id_slug(self, request):
        """Resolve a legacy numeric chat pk to slug (bookmark/notification migration)."""
        raw = request.query_params.get('id')
        if not raw or not str(raw).isdigit():
            return Response({'error': 'id is required'}, status=status.HTTP_400_BAD_REQUEST)
        chat = get_object_or_404(
            ChatService.get_user_chats(request.user, None),
            pk=int(raw),
        )
        return Response({'id': chat.id, 'slug': chat.slug})
    
    def create(self, request, *args, **kwargs):
        """
        Create a new chat (private or group).
        
        Body:
        - project: Project ID
        - type: 'private' or 'group'
        - name: Chat name (required for group chats)
        - participant_ids: List of user IDs
        """
        logger.info(f"User {request.user.id} creating chat: {request.data}")
        
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chat = serializer.save()

        # Notify all participants about the new chat via WebSocket
        self._notify_chat_created(chat, request)
        
        # Return full chat details
        response_serializer = ChatSerializer(chat, context={'request': request})
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)
    
    def _notify_chat_created(self, chat, request):
        """Send WebSocket notification to all participants about new chat"""
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync
        
        try:
            channel_layer = get_channel_layer()
            if not channel_layer:
                logger.warning("Channel layer not available for chat notification")
                return
            
            # Build chat data for notification
            chat_data = {
                'id': chat.id,
                'type': chat.type,
                'name': chat.name,
                'project': chat.project.id,
                'visibility': chat.visibility,
                'created_at': chat.created_at.isoformat(),
                'participants': [
                    {
                        'id': p.id,
                        'user': {
                            'id': p.user.id,
                            'username': p.user.username,
                            'email': p.user.email,
                        },
                        'joined_at': p.joined_at.isoformat() if p.joined_at else None,
                        'is_manager': p.is_manager,
                    }
                    for p in chat.participants.filter(is_active=True).select_related('user')
                ],
                'unread_count': 0,
                'last_message': None,
            }
            
            # Notify all participants except the creator
            for participant in chat.participants.filter(is_active=True).exclude(user=request.user):
                user_group = f'chat_user_{participant.user.id}'
                async_to_sync(channel_layer.group_send)(
                    user_group,
                    {
                        'type': 'chat_created',
                        'chat': chat_data,
                    }
                )
                logger.info(f"Notified user {participant.user.id} about new chat {chat.id}")
        
        except Exception as e:
            logger.error(f"Failed to notify participants about new chat: {e}")
    def retrieve(self, request, *args, **kwargs):
        """Get chat details"""
        chat = self.get_object()
        
        # Verify user is a participant
        if not ChatParticipant.objects.filter(
            chat=chat,
            user=request.user,
            is_active=True
        ).exists():
            logger.warning(f"User {request.user.id} attempted to access chat {chat.id} without permission")
            return Response(
                {'error': 'You are not a participant of this chat'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = self.get_serializer(chat)
        return Response(serializer.data)
    
    def destroy(self, request, *args, **kwargs):
        """
        Leave a chat (soft delete current user from chat participants).
        """
        chat = self.get_object()
        
        try:
            ChatService.leave_chat(chat, request.user)
            logger.info(f"User {request.user.id} left chat {chat.id}")
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ValueError as e:
            logger.warning(f"Failed to remove user {request.user.id} from chat {chat.id}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def add_participant(self, request, slug=None):
        """
        Add a participant to a group chat.
        
        Body:
        - user_id: ID of user to add
        """
        chat = self.get_object()
        user_id = request.data.get('user_id')
        
        if not user_id:
            return Response(
                {'error': 'user_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = User.objects.get(id=user_id)
            
            participant = ChatService.add_participant(chat, user, request.user)
            
            serializer = ChatParticipantSerializer(participant)
            logger.info(f"User {request.user.id} added user {user_id} to chat {chat.id}")
            return Response(serializer.data, status=status.HTTP_201_CREATED)
            
        except User.DoesNotExist:
            return Response(
                {'error': 'User not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except ValueError as e:
            logger.warning(f"Failed to add user {user_id} to chat {chat.id}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def remove_participant(self, request, slug=None):
        """
        Remove a participant from a group chat.
        
        Body:
        - user_id: ID of user to remove
        """
        chat = self.get_object()
        user_id = request.data.get('user_id')
        
        if not user_id:
            return Response(
                {'error': 'user_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Only channel managers can remove others.
        if not self._is_channel_manager(chat, request.user):
            return Response(
                {'error': 'Only channel managers can remove members'},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = User.objects.get(id=user_id)

            if chat.created_by_id and user.id == chat.created_by_id and request.user.id != user.id:
                return Response({'error': 'The channel creator cannot be removed by another manager'}, status=status.HTTP_400_BAD_REQUEST)
            target_participant = ChatParticipant.objects.filter(chat=chat, user=user, is_active=True).first()
            if target_participant and target_participant.is_manager:
                active_manager_count = ChatParticipant.objects.filter(chat=chat, is_active=True, is_manager=True).count()
                if active_manager_count <= 1:
                    return Response({'error': 'A channel must have at least one manager'}, status=status.HTTP_400_BAD_REQUEST)

            ChatService.remove_participant(chat, user, request.user)
            logger.info(f"User {request.user.id} removed user {user_id} from chat {chat.id}")
            return Response(status=status.HTTP_204_NO_CONTENT)

        except User.DoesNotExist:
            return Response(
                {'error': 'User not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except ValueError as e:
            logger.warning(f"Failed to remove user {user_id} from chat {chat.id}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['patch'], url_path='manager')
    def set_manager(self, request, slug=None):
        """
        Promote or demote a channel participant as manager.

        Body:
        - user_id: participant user id
        - is_manager: boolean
        """
        chat = self.get_object()
        if chat.type != ChatType.GROUP:
            return Response({'error': 'Only group chats have managers'}, status=status.HTTP_400_BAD_REQUEST)

        is_request_manager = self._is_channel_manager(chat, request.user)
        if not is_request_manager:
            return Response({'error': 'Only channel managers can assign managers'}, status=status.HTTP_403_FORBIDDEN)

        user_id = request.data.get('user_id')
        is_manager = request.data.get('is_manager')
        if user_id is None or is_manager is None:
            return Response({'error': 'user_id and is_manager are required'}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(is_manager, bool):
            return Response({'error': 'is_manager must be a boolean'}, status=status.HTTP_400_BAD_REQUEST)

        participant = ChatParticipant.objects.filter(chat=chat, user_id=user_id, is_active=True).select_related('user').first()
        if not participant:
            return Response({'error': 'User is not a participant'}, status=status.HTTP_404_NOT_FOUND)

        request_participant = ChatParticipant.objects.filter(chat=chat, user=request.user, is_active=True).first()
        had_explicit_manager = ChatParticipant.objects.filter(chat=chat, is_active=True, is_manager=True).exists()
        request_user_is_legacy_manager = bool(
            not chat.created_by_id
            and not had_explicit_manager
            and request_participant
            and ChatService.get_fallback_manager_user_id(chat) == request.user.id
        )

        if chat.created_by_id and participant.user_id == chat.created_by_id and not is_manager:
            return Response({'error': 'The channel creator must remain a manager'}, status=status.HTTP_400_BAD_REQUEST)

        if is_manager and not participant.is_manager:
            assigned_manager_count = ChatParticipant.objects.filter(
                chat=chat,
                is_active=True,
                is_manager=True,
            ).exclude(user_id=chat.created_by_id).count()
            if assigned_manager_count >= 5 and participant.user_id != chat.created_by_id:
                return Response({'error': 'A channel can have at most 5 assigned managers'}, status=status.HTTP_400_BAD_REQUEST)

        if not is_manager and participant.is_manager:
            active_manager_count = ChatParticipant.objects.filter(chat=chat, is_active=True, is_manager=True).count()
            if active_manager_count <= 1:
                return Response({'error': 'A channel must have at least one manager'}, status=status.HTTP_400_BAD_REQUEST)

        if request_user_is_legacy_manager and request_participant and not request_participant.is_manager:
            request_participant.is_manager = True
            request_participant.save(update_fields=['is_manager', 'updated_at'])

        participant.is_manager = is_manager
        participant.save(update_fields=['is_manager', 'updated_at'])
        return Response(ChatParticipantSerializer(participant).data)
    
    @action(detail=True, methods=['post'])
    def mark_as_read(self, request, slug=None):
        """
        Mark all messages in a chat as read (up to a specific message).

        Body (optional):
        - message_id: Mark messages up to this message (inclusive)
        """
        chat = self.get_object()

        serializer = MarkAsReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        message_id = serializer.validated_data.get('message_id')
        message = None

        if message_id:
            message = get_object_or_404(Message, id=message_id, chat=chat)

        try:
            MessageService.mark_chat_as_read(chat, request.user, message)
            logger.info(f"User {request.user.id} marked chat {chat.id} as read")
            return Response({'status': 'success'})
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['patch'], url_path='update_details')
    def update_details(self, request, slug=None):
        """
        Update channel name, topic, and/or description.
        Only participants may call this; only group chats support name changes.

        Body (all optional):
        - name: New channel name (group chats only)
        - topic: Short topic line
        - description: Longer description
        """
        chat = self.get_object()

        # Verify user is a participant
        if not ChatParticipant.objects.filter(chat=chat, user=request.user, is_active=True).exists():
            return Response({'error': 'You are not a participant of this chat'}, status=status.HTTP_403_FORBIDDEN)
        if chat.type == ChatType.GROUP and not self._is_channel_manager(chat, request.user):
            return Response({'error': 'Only channel managers can update channel details'}, status=status.HTTP_403_FORBIDDEN)

        # Only group chats can have their name changed via this endpoint
        data = request.data.copy()
        if chat.type != ChatType.GROUP:
            data.pop('name', None)
            data.pop('visibility', None)

        serializer = ChatUpdateSerializer(chat, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        chat = serializer.save()
        logger.info(f"User {request.user.id} updated details for chat {chat.id}")

        return Response(ChatSerializer(chat, context={'request': request}).data)

    @action(detail=True, methods=['get'], url_path='pins')
    def list_pins(self, request, slug=None):
        """List pinned messages for a channel (participant-only)."""
        chat = self.get_object()
        if not ChatParticipant.objects.filter(chat=chat, user=request.user, is_active=True).exists():
            return Response({'error': 'You are not a participant of this chat'}, status=status.HTTP_403_FORBIDDEN)
        # Pinning is a group-channel feature: only managers can pin/unpin, and a
        # direct message has no manager. Legacy rows created before that rule was
        # enforced would otherwise be listed here with no way to remove them.
        if chat.type != ChatType.GROUP:
            return Response([])
        pins = (
            PinnedMessage.objects.filter(
                chat=chat,
                message__is_deleted=False,
                message__is_revoked=False,
            )
            .select_related('message', 'message__sender', 'pinned_by')
            .order_by('-created_at', '-id')
        )
        serializer = PinnedMessageSerializer(pins, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='files')
    def list_files(self, request, slug=None):
        """
        List files (attachments) shared in this chat, newest first.

        Query params:
        - page: default 1
        - page_size: default 25 (max 100)
        """
        chat = self.get_object()
        if not ChatParticipant.objects.filter(chat=chat, user=request.user, is_active=True).exists():
            return Response({'error': 'You are not a participant of this chat'}, status=status.HTTP_403_FORBIDDEN)

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 25))
        page = max(page, 1)
        page_size = max(1, min(page_size, 100))

        queryset = (
            MessageAttachment.objects.filter(
                message__isnull=False,
                message__chat=chat,
                message__is_revoked=False,
                message__is_deleted=False,
            )
            .exclude(message__hidden_by_users=request.user)
            .exclude(
                Q(message__forwarded_from_message__isnull=False)
                | Q(message__forwarded_from_sender_display__isnull=False)
                | Q(message__forwarded_from_created_at__isnull=False)
            )
            .select_related('uploader', 'message__chat')
            .order_by('-created_at')
        )

        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        rows = queryset[start:end]
        serializer = AttachmentFileListRowSerializer(rows, many=True, context={'request': request})
        return Response({
            'results': serializer.data,
            'page': page,
            'page_size': page_size,
            'total': total,
        })

    @action(detail=True, methods=['post'], url_path='pin')
    def pin_message(self, request, slug=None):
        """Pin a message in a channel. Body: { message_id }"""
        chat = self.get_object()
        if not ChatParticipant.objects.filter(chat=chat, user=request.user, is_active=True).exists():
            return Response({'error': 'You are not a participant of this chat'}, status=status.HTTP_403_FORBIDDEN)
        if chat.type != ChatType.GROUP or not self._is_channel_manager(chat, request.user):
            return Response({'error': 'Only channel managers can pin messages'}, status=status.HTTP_403_FORBIDDEN)

        request_serializer = PinMessageRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        message_id = request_serializer.validated_data['message_id']

        try:
            message = Message.objects.get(
                id=message_id,
                chat=chat,
                is_deleted=False,
                is_revoked=False,
            )
        except Message.DoesNotExist:
            return Response({'error': 'Message not found'}, status=status.HTTP_404_NOT_FOUND)
        pin, created = PinnedMessage.objects.get_or_create(
            chat=chat, message=message,
            defaults={'pinned_by': request.user},
        )
        pin_data = PinnedMessageSerializer(pin, context={'request': request}).data
        if created:
            self._notify_pin_update(chat, 'pinned', message.id, pin_data)
        return Response(
            pin_data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=['delete'], url_path='pin/(?P<message_id>[^/.]+)')
    def unpin_message(self, request, slug=None, message_id=None):
        """Unpin a message from a channel."""
        chat = self.get_object()
        if not ChatParticipant.objects.filter(chat=chat, user=request.user, is_active=True).exists():
            return Response({'error': 'You are not a participant of this chat'}, status=status.HTTP_403_FORBIDDEN)
        if chat.type != ChatType.GROUP or not self._is_channel_manager(chat, request.user):
            return Response({'error': 'Only channel managers can unpin messages'}, status=status.HTTP_403_FORBIDDEN)

        request_serializer = PinMessageRequestSerializer(data={'message_id': message_id})
        request_serializer.is_valid(raise_exception=True)
        validated_message_id = request_serializer.validated_data['message_id']

        deleted, _ = PinnedMessage.objects.filter(
            chat=chat,
            message_id=validated_message_id,
        ).delete()
        if not deleted:
            return Response({'error': 'Pin not found'}, status=status.HTTP_404_NOT_FOUND)
        self._notify_pin_update(chat, 'unpinned', validated_message_id)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['get'], url_path='browse')
    def browse(self, request):
        """
        List all group chats in a project that the current user can see,
        annotated with whether they are already a member.

        Query params: project_id (required)
        """
        project_id = request.query_params.get('project_id')
        if not project_id:
            return Response({'error': 'project_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        project_id = resolve_project_pk(project_id)
        if project_id is None:
            return Response({'error': 'Invalid project_id'}, status=status.HTTP_400_BAD_REQUEST)

        if not ProjectMember.objects.filter(project_id=project_id, user=request.user, is_active=True).exists():
            return Response({'error': 'You are not a member of this project'}, status=status.HTTP_403_FORBIDDEN)

        # All group chats in this project
        chats = Chat.objects.filter(
            project_id=project_id,
            type=ChatType.GROUP,
            visibility=ChannelVisibility.PUBLIC,
        ).order_by('name')

        user_chat_ids = set(
            ChatParticipant.objects.filter(
                user=request.user, is_active=True
            ).values_list('chat_id', flat=True)
        )

        results = []
        for chat in chats:
            results.append({
                'id': chat.id,
                'slug': chat.slug,
                'name': chat.name or 'Unnamed',
                'topic': chat.topic,
                'description': chat.description,
                'visibility': chat.visibility,
                'participant_count': chat.participants.filter(is_active=True).count(),
                'is_member': chat.id in user_chat_ids,
            })

        return Response(results)

    @action(detail=True, methods=['patch'], url_path='notification_settings')
    def notification_settings(self, request, slug=None):
        """
        Update the current user's notification preferences for this chat.
        Body (all optional): { is_muted, notification_level }
        """
        chat = self.get_object()
        try:
            participant = ChatParticipant.objects.get(chat=chat, user=request.user, is_active=True)
        except ChatParticipant.DoesNotExist:
            return Response({'error': 'You are not a participant of this chat'}, status=status.HTTP_403_FORBIDDEN)
        serializer = ParticipantNotificationSerializer(participant, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class MessageViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing messages.
    
    Endpoints:
    - GET /messages/?chat_id=X - List messages for a chat (with cursor pagination)
    - POST /messages/ - Send a message
    - GET /messages/{id}/ - Get message details
    """
    
    permission_classes = [IsAuthenticated]
    serializer_class = MessageSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_action_scopes = {
        'create': 'chat_message_write',
        'forward_batch': 'chat_message_write',
        'partial_update': 'chat_message_write',
        'update': 'chat_message_write',
        'destroy': 'chat_message_write',
        'hide': 'chat_message_write',
        'revoke': 'chat_message_write',
        'react': 'chat_reaction',
        'remove_reaction': 'chat_reaction',
    }

    def get_throttles(self):
        self.throttle_scope = self.throttle_action_scopes.get(getattr(self, 'action', None))
        if not self.throttle_scope:
            return []
        return super().get_throttles()

    def _message_queryset(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        thread_replies_for_summary = (
            Message.objects
            .select_related('sender')
            .only(
                'id',
                'chat_id',
                'parent_message_id',
                'sender_id',
                'created_at',
                'sender__id',
                'sender__username',
                'sender__email',
                'sender__avatar',
            )
            .order_by('created_at')
        )
        return Message.objects.select_related(
            'sender',
            'chat',
            'chat__project',
            'reply_to',
            'reply_to__sender',
            'forwarded_from_message',
        ).prefetch_related(
            'attachments',
            'reply_to__attachments',
            'mentions__mentioned_user',
            'reactions__user',
            'statuses',
            Prefetch(
                'thread_replies',
                queryset=thread_replies_for_summary,
                to_attr='_thread_replies_for_summary',
            ),
            # Only the viewer's own row matters, so fetch just that one and let the
            # serializer read it off the instance instead of asking per message.
            Prefetch(
                'link_preview_hidden_by',
                queryset=User.objects.filter(id=self.request.user.id).only('id'),
                to_attr='_link_preview_hidden_for_viewer',
            ),
        )
    
    def get_queryset(self):
        """Get messages for a specific chat"""
        # For retrieve/detail actions, return all messages (permission checked in retrieve method)
        if self.action in ['retrieve', 'mark_as_read', 'react', 'remove_reaction', 'remind', 'cancel_remind', 'revoke', 'destroy', 'hide', 'hide_link_preview', 'partial_update', 'update', 'thread_replies', 'mark_thread_as_read']:
            return self._message_queryset()

        # For list action, require chat_id
        chat_id = self.request.query_params.get('chat_id')

        if not chat_id:
            return Message.objects.none()

        # Verify user is a participant
        if not ChatParticipant.objects.filter(
            chat_id=chat_id,
            user=self.request.user,
            is_active=True
        ).exists():
            return Message.objects.none()

        # Filter out messages hidden by current user
        return self._message_queryset().filter(
            chat_id=chat_id,
            is_deleted=False
        ).exclude(
            hidden_by_users=self.request.user
        ).select_related('sender').order_by('-created_at')
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'create':
            return MessageCreateWithAttachmentsSerializer
        if self.action == 'forward_batch':
            return ForwardBatchSerializer
        return MessageWithAttachmentsSerializer
    
    def list(self, request, *args, **kwargs):
        """
        List messages with cursor-based pagination.
        
        Query params:
        - chat_id: Chat ID (required)
        - before: Get messages before this timestamp (ISO format)
        - after: Get messages after this timestamp (ISO format)
        - page_size: Number of messages (default: 20, max: 100)
        """
        chat_id = request.query_params.get('chat_id')
        
        if not chat_id:
            return Response(
                {'error': 'chat_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            chat = Chat.objects.get(id=chat_id)
        except Chat.DoesNotExist:
            return Response(
                {'error': 'Chat not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Parse cursor parameters
        before_str = request.query_params.get('before')
        after_str = request.query_params.get('after')
        page_size = min(int(request.query_params.get('page_size', 20)), 100)
        
        before = None
        after = None
        
        if before_str:
            try:
                before = datetime.fromisoformat(before_str.replace('Z', '+00:00'))
            except ValueError:
                return Response(
                    {'error': 'Invalid before timestamp format'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        if after_str:
            try:
                after = datetime.fromisoformat(after_str.replace('Z', '+00:00'))
            except ValueError:
                return Response(
                    {'error': 'Invalid after timestamp format'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        try:
            messages = MessageService.get_chat_messages(
                chat,
                request.user,
                before=before,
                after=after,
                limit=page_size
            )
            
            serializer = self.get_serializer(messages, many=True)
            
            # Generate cursors for pagination
            data = serializer.data
            next_cursor = None
            prev_cursor = None
            
            if data:
                # For "before" queries (scrolling up), reverse the order
                if not after:
                    data = list(reversed(data))
                
                # Set cursors
                if len(data) == page_size:
                    # There might be more messages
                    if after:
                        next_cursor = data[-1]['created_at']
                    else:
                        prev_cursor = data[0]['created_at']
            
            return Response({
                'results': data,
                'next_cursor': next_cursor,
                'prev_cursor': prev_cursor,
                'page_size': page_size
            })
            
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)
    
    def create(self, request, *args, **kwargs):
        """
        Send a message to a chat.
        
        Body:
        - chat: Chat ID
        - content: Message content (optional if attachments present)
        - attachment_ids: List of attachment IDs to link (optional)
        """
        logger.info(f"User {request.user.id} sending message to chat {request.data.get('chat')}")
        
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            message = serializer.save()
            created = getattr(serializer, '_message_created', True)

            # Keep the create response independent of channel size. The sender
            # only needs the committed message; recipient statuses are durable
            # server-side delivery bookkeeping and are available on normal
            # message reads. Serializing 99 nested users for every send caused
            # large responses and kept ASGI database connections occupied.
            message = Message.objects.select_related(
                'sender', 'reply_to', 'reply_to__sender', 'forwarded_from_message'
            ).prefetch_related(
                'attachments',
                'reply_to__attachments',
                'mentions__mentioned_user',
            ).get(id=message.id)

            response_serializer = MessageWithAttachmentsSerializer(
                message,
                context={
                    'request': request,
                    'send_response': True,
                },
            )
            logger.info(
                "Message %s %s successfully with %s attachments",
                message.id,
                'created' if created else 'deduped',
                message.attachments.count(),
            )
            response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
            return Response(response_serializer.data, status=response_status)
            
        except ValueError as e:
            logger.warning(f"Failed to create message: {e}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    
    def partial_update(self, request, *args, **kwargs):
        from .services import extract_message_plain_text, sync_message_mentions
        message = self.get_object()
        if message.sender != request.user:
            return Response({'error': 'You can only edit your own messages'}, status=status.HTTP_403_FORBIDDEN)

        data = request.data.copy()

        # If rich_body supplied, re-derive plain content automatically
        rich_body = data.get('rich_body')
        if rich_body and not data.get('content'):
            data['content'] = extract_message_plain_text(rich_body)

        normalized_mention_ids = None
        if 'mention_ids' in request.data:
            mention_ids = request.data.get('mention_ids', [])
            if not isinstance(mention_ids, list):
                return Response(
                    {'mention_ids': 'Mentioned users must be sent as a list.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            normalized_mention_ids = [int(uid) for uid in mention_ids]
            if len(normalized_mention_ids) != len(set(normalized_mention_ids)):
                return Response(
                    {'mention_ids': 'Duplicate mentioned users are not allowed.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            valid_ids = set(
                ChatParticipant.objects.filter(
                    chat=message.chat,
                    is_active=True,
                    user_id__in=normalized_mention_ids,
                ).values_list('user_id', flat=True)
            )
            if set(normalized_mention_ids) - valid_ids:
                return Response(
                    {'mention_ids': 'Mentioned users must be active participants in this chat.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        serializer = self.get_serializer(message, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(is_edited=True)

        # Sync mentions only when the edit request intentionally supplies them.
        if normalized_mention_ids is not None:
            sync_message_mentions(message, normalized_mention_ids)

        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        """Get message details"""
        message = self.get_object()
        
        # Verify user is a participant of the chat
        if not ChatParticipant.objects.filter(
            chat=message.chat,
            user=request.user,
            is_active=True
        ).exists():
            return Response(
                {'error': 'You are not a participant of this chat'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = self.get_serializer(message)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def mark_as_read(self, request, pk=None):
        """
        Mark a specific message as read.
        """
        message = self.get_object()
        
        try:
            MessageService.mark_message_as_read(message, request.user)
            logger.info(f"User {request.user.id} marked message {message.id} as read")
            return Response({'status': 'success'})
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        """
        Get unread message count for current user.
        
        Query params:
        - chat_id: Get unread count for specific chat (optional)
        """
        chat_id = request.query_params.get('chat_id')
        chat = None
        
        if chat_id:
            try:
                chat = Chat.objects.get(id=chat_id)
            except Chat.DoesNotExist:
                return Response(
                    {'error': 'Chat not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        count = MessageService.get_unread_count(request.user, chat)
        
        return Response({
            'unread_count': count,
            'chat_id': chat_id
        })

    @action(detail=True, methods=['get'], url_path='thread_replies')
    def thread_replies(self, request, pk=None):
        """
        List the thread replies for a root message.

        GET /api/chat/messages/{id}/thread_replies/

        Returns replies in ascending chronological order.
        Also marks the thread as read for the current user.
        """
        root = get_object_or_404(Message, pk=pk)

        # Access check: user must be a chat participant
        if not ChatParticipant.objects.filter(
            chat=root.chat,
            user=request.user,
            is_active=True,
        ).exists():
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        replies = (
            Message.objects.filter(parent_message=root, chat=root.chat)
            .select_related('sender', 'reply_to', 'reply_to__sender')
            .prefetch_related('attachments', 'mentions__mentioned_user', 'reactions__user')
            .order_by('created_at')
        )

        serializer = MessageWithAttachmentsSerializer(replies, many=True, context={'request': request})
        return Response({'results': serializer.data})

    @action(detail=True, methods=['post'], url_path='mark_thread_as_read')
    def mark_thread_as_read(self, request, pk=None):
        """
        Mark all current thread replies for a root message as read by the current user.

        POST /api/chat/messages/{id}/mark_thread_as_read/
        """
        from django.utils import timezone as tz
        from .models import ThreadReadStatus

        root = get_object_or_404(Message, pk=pk)

        # Access check
        if not ChatParticipant.objects.filter(
            chat=root.chat,
            user=request.user,
            is_active=True,
        ).exists():
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        last_reply = root.thread_replies.filter(chat=root.chat).order_by('-created_at').first()
        if last_reply:
            ThreadReadStatus.objects.update_or_create(
                user=request.user,
                root_message=root,
                defaults={'last_read_at': last_reply.created_at},
            )

        return Response({'status': 'ok'})

    @action(detail=False, methods=['post'])
    def forward_batch(self, request):
        """
        Forward multiple messages to multiple chats/users in one request.

        Supports partial success and returns detailed failure records.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        try:
            result = MessageService.forward_messages_batch(
                source_chat_id=data['source_chat_id'],
                source_message_ids=data['source_message_ids'],
                target_chat_ids=data.get('target_chat_ids', []),
                target_user_ids=data.get('target_user_ids', []),
                user=request.user
            )

            if result['status'] in ['success', 'partial_success']:
                return Response(result, status=status.HTTP_200_OK)

            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def react(self, request, pk=None):
        """
        Add or toggle a reaction on a message.

        If the user already has this reaction, it will be removed (toggle behavior).

        Body:
        - emoji: The emoji character to react with
        """
        message = self.get_object()

        # Verify user is a participant of the chat
        if not ChatParticipant.objects.filter(
            chat=message.chat,
            user=request.user,
            is_active=True
        ).exists():
            return Response(
                {'error': 'You are not a participant of this chat'},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = AddReactionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        emoji = serializer.validated_data['emoji']

        # Check if reaction already exists
        existing = MessageReaction.objects.filter(
            message=message,
            user=request.user,
            emoji=emoji
        ).first()

        if existing:
            # Toggle off - remove reaction
            existing.delete()
            action_taken = 'removed'
        else:
            # Add new reaction
            MessageReaction.objects.create(
                message=message,
                user=request.user,
                emoji=emoji
            )
            action_taken = 'added'

        # Notify via WebSocket
        from .tasks import notify_reaction_update
        notify_reaction_update.delay(
            message.id,
            request.user.id,
            emoji,
            action_taken,
            tenant_schema=current_tenant_schema(),
        )

        # Return updated reactions
        message.refresh_from_db()
        response_serializer = MessageWithAttachmentsSerializer(message, context={'request': request})
        return Response({
            'status': action_taken,
            'message': response_serializer.data
        })

    @action(detail=True, methods=['delete'], url_path='react/(?P<emoji>[^/.]+)')
    def remove_reaction(self, request, pk=None, emoji=None):
        """
        Remove a specific reaction from a message.

        URL params:
        - emoji: The emoji character to remove (URL encoded)
        """
        message = self.get_object()

        # Verify user is a participant of the chat
        if not ChatParticipant.objects.filter(
            chat=message.chat,
            user=request.user,
            is_active=True
        ).exists():
            return Response(
                {'error': 'You are not a participant of this chat'},
                status=status.HTTP_403_FORBIDDEN
            )

        if not emoji:
            return Response(
                {'error': 'Emoji is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Find and delete the reaction
        deleted_count, _ = MessageReaction.objects.filter(
            message=message,
            user=request.user,
            emoji=emoji
        ).delete()

        if deleted_count == 0:
            return Response(
                {'error': 'Reaction not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Notify via WebSocket
        from .tasks import notify_reaction_update
        notify_reaction_update.delay(
            message.id,
            request.user.id,
            emoji,
            'removed',
            tenant_schema=current_tenant_schema(),
        )

        # Return updated reactions
        message.refresh_from_db()
        response_serializer = MessageWithAttachmentsSerializer(message, context={'request': request})
        return Response({
            'status': 'removed',
            'message': response_serializer.data
        })

    @action(detail=True, methods=['post'])
    def remind(self, request, pk=None):
        """
        Set or update a reminder for a message.

        Body:
        - remind_at: When to send the reminder (ISO 8601 datetime)
        - note: Optional note for the reminder (max 255 chars)
        """
        from .models import MessageReminder
        from .serializers import SetReminderSerializer

        message = self.get_object()

        # Verify user is a participant of the chat
        if not ChatParticipant.objects.filter(
            chat=message.chat,
            user=request.user,
            is_active=True
        ).exists():
            return Response(
                {'error': 'You are not a participant of this chat'},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = SetReminderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        remind_at = serializer.validated_data['remind_at']
        note = serializer.validated_data.get('note', '')

        # Create or update reminder
        reminder, created = MessageReminder.objects.update_or_create(
            message=message,
            user=request.user,
            defaults={
                'remind_at': remind_at,
                'note': note,
                'is_sent': False,
                'sent_at': None,
            }
        )

        logger.info(
            f"User {request.user.id} {'created' if created else 'updated'} reminder for message {message.id} at {remind_at}"
        )

        return Response({
            'status': 'created' if created else 'updated',
            'reminder': {
                'id': reminder.id,
                'remind_at': reminder.remind_at.isoformat(),
                'note': reminder.note,
            }
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=True, methods=['delete'])
    def cancel_remind(self, request, pk=None):
        """
        Cancel a reminder for a message.

        DELETE /api/chat/messages/{id}/cancel_remind/
        """
        from .models import MessageReminder

        message = self.get_object()

        # Verify user is a participant of the chat
        if not ChatParticipant.objects.filter(
            chat=message.chat,
            user=request.user,
            is_active=True
        ).exists():
            return Response(
                {'error': 'You are not a participant of this chat'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Delete the reminder
        deleted_count, _ = MessageReminder.objects.filter(
            message=message,
            user=request.user
        ).delete()

        if deleted_count == 0:
            return Response(
                {'error': 'No reminder found'},
                status=status.HTTP_404_NOT_FOUND
            )

        logger.info(f"User {request.user.id} cancelled reminder for message {message.id}")

        return Response({'status': 'cancelled'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def revoke(self, request, pk=None):
        """
        Revoke a message within the configured revoke window.

        Rules:
        - Only sender can revoke
        - Must be inside the configured revoke window
        - Cannot revoke already revoked message
        """
        from django.utils import timezone
        from datetime import timedelta

        message = self.get_object()

        # Verify user is sender
        if message.sender != request.user:
            return Response(
                {'error': 'Only the sender can revoke this message'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Check if already revoked
        if message.is_revoked:
            return Response(
                {'error': 'Message is already revoked'},
                status=status.HTTP_400_BAD_REQUEST
            )

        revoke_window_minutes = settings.CHAT_REVOKE_WINDOW_MINUTES
        time_limit = timezone.now() - timedelta(minutes=revoke_window_minutes)
        if message.created_at <= time_limit:
            return Response(
                {'error': f'Message can only be revoked within {revoke_window_minutes} minutes of sending'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Revoke the message
        message.is_revoked = True
        message.revoked_at = timezone.now()
        message.save(update_fields=['is_revoked', 'revoked_at', 'updated_at'])
        # A revoked message is filtered out of the pin list, so the row has to
        # go — and members holding it on screen have to be told, or their
        # banner keeps offering a jump to a message that is no longer there.
        unpinned, _ = PinnedMessage.objects.filter(message=message).delete()
        if unpinned:
            queue_pin_update(
                message.chat_id,
                'unpinned',
                message.id,
                actor_user_id=request.user.id,
            )

        logger.info(f"User {request.user.id} revoked message {message.id}")

        # Update related notifications
        try:
            from notifications.models import Notification, NotificationCategory

            # Find all unread chat notifications for this chat
            notifications = Notification.objects.filter(
                category=NotificationCategory.COLLABORATION,
                event_type='chat_new_message',
                related_object_id=str(message.chat_id),
                is_read=False
            )

            logger.info(f"Found {notifications.count()} unread notifications for chat {message.chat_id}")

            for notification in notifications:
                # Recalculate unread message count for this recipient
                from .models import ChatParticipant
                try:
                    participant = ChatParticipant.objects.get(
                        chat_id=message.chat_id,
                        user_id=notification.recipient_id,
                        is_active=True
                    )
                    unread_count = participant.get_unread_count()
                except ChatParticipant.DoesNotExist:
                    unread_count = 0

                # Find the latest UNREAD non-revoked message (not all messages)
                query = Message.objects.filter(
                    chat_id=message.chat_id,
                    is_deleted=False,
                    is_revoked=False
                ).exclude(sender=notification.recipient)

                # Only consider unread messages
                try:
                    if participant.last_read_at:
                        query = query.filter(created_at__gt=participant.last_read_at)
                    latest_unread_message = query.order_by('-created_at').first()
                except:
                    latest_unread_message = None

                sender_name = request.user.username or request.user.email or 'User'

                # Update notification content based on whether we have unread messages
                if latest_unread_message:
                    # Show the latest unread message
                    notification.body = latest_unread_message.content or '[Attachment]'
                    notification.metadata['message_id'] = latest_unread_message.id
                    notification.metadata['message_preview'] = latest_unread_message.content or '[Attachment]'
                    if 'is_recalled' in notification.metadata:
                        del notification.metadata['is_recalled']
                else:
                    # No unread messages left, show recalled message
                    notification.body = f"{sender_name} recalled a message"
                    notification.metadata['message_preview'] = 'recalled a message'
                    notification.metadata['message_id'] = message.id
                    notification.metadata['is_recalled'] = True

                # Update message count
                notification.metadata['message_count'] = unread_count

                # Mark as read if no unread messages
                if unread_count == 0:
                    notification.is_read = True

                # Always save the notification
                logger.info(f"Updated notification {notification.id} - unread_count={unread_count}, is_read={notification.is_read}, body={notification.body[:50] if len(notification.body) > 50 else notification.body}")
                notification.save()

                # Send SSE update to notify frontend
                try:
                    from notifications.services import send_notification_update
                    send_notification_update(notification.recipient_id, notification)
                except Exception as e:
                    logger.warning(f"Failed to send SSE notification update: {e}")
        except Exception as e:
            logger.error(f"Failed to update notifications after revoke: {e}")

        # Return updated message
        response_serializer = MessageWithAttachmentsSerializer(message, context={'request': request})
        return Response({
            'status': 'revoked',
            'message': response_serializer.data
        }, status=status.HTTP_200_OK)

    def destroy(self, request, *args, **kwargs):
        """
        Delete a message for everyone by soft-deleting it.

        Rules:
        - Only sender can delete their own messages
        - Keep a tombstone row in the timeline
        """
        message = self.get_object()

        # Verify user is sender
        if message.sender != request.user:
            return Response(
                {'error': 'Only the sender can delete this message'},
                status=status.HTTP_403_FORBIDDEN
            )

        message_id = message.id

        if not message.is_deleted:
            message.is_deleted = True
            message.deleted_at = timezone.now()
            message.content = ''
            message.rich_body = None
            message.has_attachments = False
            message.is_edited = False
            message.save(update_fields=[
                'is_deleted',
                'deleted_at',
                'content',
                'rich_body',
                'has_attachments',
                'is_edited',
                'updated_at',
            ])

        # Same reasoning as revoke: dropping the row silently leaves every
        # other member's pinned list pointing at a deleted message.
        unpinned, _ = PinnedMessage.objects.filter(message=message).delete()
        if unpinned:
            queue_pin_update(
                message.chat_id,
                'unpinned',
                message.id,
                actor_user_id=request.user.id,
            )

        logger.info(f"User {request.user.id} soft-deleted message {message_id}")

        response_serializer = MessageWithAttachmentsSerializer(message, context={'request': request})
        return Response({
            'status': 'deleted',
            'message': response_serializer.data,
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def hide(self, request, pk=None):
        """
        Hide a message for the current user only (does not affect other users).

        Rules:
        - Any user can hide any message in chats they participate in
        - Message remains visible to other participants
        - Hidden messages are filtered from list queries
        """
        message = self.get_object()

        # Verify user is a participant in this chat
        if not ChatParticipant.objects.filter(
            chat=message.chat,
            user=request.user,
            is_active=True
        ).exists():
            return Response(
                {'error': 'You are not a participant in this chat'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Add user to hidden_by_users
        message.hidden_by_users.add(request.user)

        logger.info(f"User {request.user.id} hid message {message.id}")

        # Return the updated message
        serializer = self.get_serializer(message)
        return Response({'status': 'hidden', 'message': serializer.data}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def hide_link_preview(self, request, pk=None):
        """Dismiss this message's link preview card, for the current user only.

        A view preference, not an edit: the message stays, other participants keep
        their card, and the URL-keyed preview cache is untouched — the same link
        still previews in other messages and is never re-fetched because of this.
        """
        message = self.get_object()

        if not ChatParticipant.objects.filter(
            chat=message.chat,
            user=request.user,
            is_active=True
        ).exists():
            return Response(
                {'error': 'You are not a participant in this chat'},
                status=status.HTTP_403_FORBIDDEN
            )

        # add() is idempotent, so dismissing twice is harmless.
        message.link_preview_hidden_by.add(request.user)

        logger.info(
            'User %s dismissed the link preview on message %s', request.user.id, message.id
        )
        serializer = self.get_serializer(message)
        return Response(
            {'status': 'link_preview_hidden', 'message': serializer.data},
            status=status.HTTP_200_OK,
        )


class AttachmentViewSet(viewsets.GenericViewSet):
    """
    ViewSet for managing message attachments.
    
    Endpoints:
    - POST /attachments/ - Upload a new attachment
    - GET /attachments/{id}/ - Get attachment details
    - DELETE /attachments/{id}/ - Delete an unlinked attachment
    """
    
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    serializer_class = MessageAttachmentSerializer
    # Prevent `/attachments/<pk>/` from matching non-numeric paths like `/attachments/files/`.
    lookup_value_regex = r'\d+'
    
    def get_queryset(self):
        """Get attachments uploaded by current user"""
        return MessageAttachment.objects.filter(uploader=self.request.user)
    
    def create(self, request, *args, **kwargs):
        """
        Upload a new attachment.
        
        Body (multipart/form-data):
        - file: The file to upload
        
        Returns the attachment details including the file URL.
        The attachment is initially unlinked (message=null).
        When sending a message, include the attachment IDs to link them.
        """

        uploaded_file = request.FILES.get("file")

        if uploaded_file:
            try:
                validate_attachment_mime_type(uploaded_file.content_type)
            except UnsupportedAttachmentMimeType as e:
                return Response(
                    {
                        "code": "unsupported_mime_type",
                        "error": str(e),
                        "mime_type": uploaded_file.content_type,
                    },
                    status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                )

        serializer = AttachmentUploadSerializer(
            data=request.data, 
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        attachment = serializer.save()
        
        logger.info(f"User {request.user.id} uploaded attachment {attachment.id}: {attachment.original_filename}")
        
        # Return attachment details
        response_serializer = MessageAttachmentSerializer(
            attachment, 
            context={'request': request}
        )
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)
    
    def retrieve(self, request, pk=None, *args, **kwargs):
        """Get attachment details"""
        try:
            attachment = MessageAttachment.objects.get(id=pk)
            
            # Check access: user must be uploader or participant of the chat
            if attachment.uploader != request.user:
                if attachment.message:
                    if not ChatParticipant.objects.filter(
                        chat=attachment.message.chat,
                        user=request.user,
                        is_active=True
                    ).exists():
                        return Response(
                            {'error': 'You do not have access to this attachment'},
                            status=status.HTTP_403_FORBIDDEN
                        )
                else:
                    return Response(
                        {'error': 'You do not have access to this attachment'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            
            serializer = self.get_serializer(attachment)
            return Response(serializer.data)
            
        except MessageAttachment.DoesNotExist:
            return Response(
                {'error': 'Attachment not found'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    def destroy(self, request, pk=None, *args, **kwargs):
        """
        Delete an unlinked attachment.
        
        Only attachments that are not yet linked to a message can be deleted.
        This is for canceling uploads before sending.
        """
        try:
            attachment = MessageAttachment.objects.get(
                id=pk,
                uploader=request.user,
                message__isnull=True  # Only unlinked attachments
            )
            
            # Delete the file from storage
            if attachment.file:
                attachment.file.delete(save=False)
            if attachment.thumbnail:
                attachment.thumbnail.delete(save=False)
            
            attachment.delete()
            
            logger.info(f"User {request.user.id} deleted attachment {pk}")
            return Response(status=status.HTTP_204_NO_CONTENT)
            
        except MessageAttachment.DoesNotExist:
            return Response(
                {'error': 'Attachment not found or already linked to a message'},
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=False, methods=['get'], url_path='files')
    def files(self, request):
        """
        List message attachments accessible to the current user for a project.

        Query params:
        - project_id: required
        - page: default 1
        - page_size: default 25
        """
        project_id = request.query_params.get('project_id')
        if not project_id:
            return Response(
                {'error': 'project_id is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        pid = resolve_project_pk(project_id)
        if pid is None:
            return Response(
                {'error': 'Invalid project_id'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 25))
        page = max(page, 1)
        page_size = max(1, min(page_size, 100))

        chat_ids = ChatParticipant.objects.filter(
            user=request.user,
            is_active=True,
            chat__project_id=pid,
        ).values_list('chat_id', flat=True)

        queryset = (
            MessageAttachment.objects.filter(
                message__isnull=False,
                message__chat_id__in=chat_ids,
                message__is_revoked=False,
                message__is_deleted=False,
            )
            .exclude(message__hidden_by_users=request.user)
            # Files tab should not surface forwarded attachment copies. The live
            # original attachment appears once; if the original message is deleted,
            # forwarded copies are hidden/cleaned up instead of becoming stale rows.
            .exclude(
                Q(message__forwarded_from_message__isnull=False)
                | Q(message__forwarded_from_sender_display__isnull=False)
                | Q(message__forwarded_from_created_at__isnull=False)
            )
            .select_related('uploader', 'message__chat')
            .order_by('-created_at')
        )

        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        rows = queryset[start:end]
        serializer = AttachmentFileListRowSerializer(rows, many=True, context={'request': request})
        return Response(
            {
                'results': serializer.data,
                'page': page,
                'page_size': page_size,
                'total': total,
            }
        )


class SavedMessageViewSet(
    mixins.ListModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Saved (bookmarked) messages for the current user.

    - GET  /saved/           list saved messages (newest first)
    - POST /saved/           save a message   body: { message_id }
    - DELETE /saved/{id}/    unsave (pk = SavedMessage.id)
    """
    permission_classes = [IsAuthenticated]
    serializer_class = SavedMessageSerializer

    def get_queryset(self):
        return SavedMessage.objects.filter(user=self.request.user).select_related(
            'message', 'message__sender', 'message__chat'
        )

    def create(self, request, *args, **kwargs):
        message_id = request.data.get('message_id')
        if not message_id:
            return Response({'error': 'message_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            message = Message.objects.get(id=message_id, is_deleted=False)
        except Message.DoesNotExist:
            return Response({'error': 'Message not found'}, status=status.HTTP_404_NOT_FOUND)
        # Verify user has access to the chat
        if not ChatParticipant.objects.filter(chat=message.chat, user=request.user, is_active=True).exists():
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        saved, created = SavedMessage.objects.get_or_create(user=request.user, message=message)
        serializer = SavedMessageSerializer(saved, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class ScheduledMessageViewSet(
    mixins.ListModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Scheduled (send-later) messages owned by the current user.

    - GET  /scheduled/?chat_id={id}   list pending scheduled messages for a chat
    - POST /scheduled/                schedule a new message  body: ScheduledMessageCreateSerializer
    - DELETE /scheduled/{id}/         cancel (must be pending)
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ScheduledMessageSerializer

    def get_queryset(self):
        qs = ScheduledMessage.objects.filter(sender=self.request.user)
        chat_id = self.request.query_params.get('chat_id')
        if chat_id:
            qs = qs.filter(chat_id=chat_id, status=ScheduledMessage.STATUS_PENDING)
        return qs.order_by('scheduled_at')

    def create(self, request, *args, **kwargs):
        ser = ScheduledMessageCreateSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        # Verify user is a participant in the target chat
        try:
            chat = Chat.objects.get(id=data['chat_id'])
        except Chat.DoesNotExist:
            return Response({'error': 'Chat not found'}, status=status.HTTP_404_NOT_FOUND)

        if not ChatParticipant.objects.filter(chat=chat, user=request.user, is_active=True).exists():
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        sm = ScheduledMessage.objects.create(
            chat=chat,
            sender=request.user,
            content=data.get('content', ''),
            rich_body=data.get('rich_body'),
            attachment_ids=data.get('attachment_ids', []),
            mention_ids=data.get('mention_ids', []),
            reply_to_id=data.get('reply_to_id'),
            scheduled_at=data['scheduled_at'],
        )

        # Dispatch Celery task with ETA
        result = send_scheduled_message.apply_async(
            args=[sm.id],
            kwargs={'tenant_schema': current_tenant_schema()},
            eta=sm.scheduled_at,
        )
        sm.task_id = result.id
        sm.save(update_fields=['task_id', 'updated_at'])

        return Response(
            ScheduledMessageSerializer(sm).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        sm = self.get_object()
        if sm.sender_id != request.user.id:
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        if sm.status != ScheduledMessage.STATUS_PENDING:
            return Response(
                {'error': f'Cannot cancel a message with status={sm.status}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Mark cancelled in DB first — the task checks this flag on execution,
        # so even if the Celery revoke never lands, the message won't be sent.
        sm.status = ScheduledMessage.STATUS_CANCELLED
        sm.save(update_fields=['status', 'updated_at'])
        # Fire-and-forget revoke in a background thread so it never blocks the
        # HTTP response (broker broadcasts can stall under load).
        task_id = sm.task_id
        if task_id:
            import threading
            def _revoke_task():
                try:
                    from celery import current_app
                    current_app.control.revoke(task_id, terminate=False, reply=False)
                except Exception as exc:
                    logger.warning(f"Could not revoke task {task_id}: {exc}")
            threading.Thread(target=_revoke_task, daemon=True).start()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def fetch_link_preview(request):
    """
    Fetch metadata from a URL for link preview.
    
    Body:
    - url: The URL to fetch metadata from
    
    Returns:
    - title: Page title
    - description: Page description
    - image: Preview image URL
    - site_name: Site name
    - url: The original URL
    """
    url = request.data.get('url')
    
    if not url:
        return Response(
            {'error': 'URL is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # SSRF guard (MED-279): the server is about to fetch a URL a user supplied, so
    # safety is decided by the *resolved IP*, not by the URL string, and redirects
    # are re-validated hop by hop inside fetch_url_safely.
    try:
        safe_url = validate_public_url(url)
    except UnsafeUrlError as exc:
        logger.warning('Refused link preview for %s: %s', url, exc)
        return Response(
            {'error': 'Invalid or disallowed URL'},
            status=status.HTTP_400_BAD_REQUEST
        )

    parsed = urlparse(safe_url)

    # Check cache first
    cache_key = f"link_preview:{safe_url}"
    cached_data = cache.get(cache_key)
    if cached_data:
        return Response(cached_data)

    try:
        html = fetch_url_safely(safe_url)

        # Parse HTML
        soup = BeautifulSoup(html, 'html.parser')
        url = safe_url  # relative og:image URLs resolve against the validated URL

        # Extract metadata
        preview_data = {
            'url': url,
            'title': None,
            'description': None,
            'image': None,
            'site_name': None,
            'type': 'website',
        }
        
        # Open Graph tags (preferred)
        og_title = soup.find('meta', property='og:title')
        og_description = soup.find('meta', property='og:description')
        og_image = soup.find('meta', property='og:image')
        og_site_name = soup.find('meta', property='og:site_name')
        og_type = soup.find('meta', property='og:type')
        
        if og_title:
            preview_data['title'] = og_title.get('content', '').strip()
        if og_description:
            preview_data['description'] = og_description.get('content', '').strip()
        if og_image:
            img_url = og_image.get('content', '').strip()
            # Make relative URLs absolute
            if img_url and not img_url.startswith(('http://', 'https://')):
                img_url = urljoin(url, img_url)
            preview_data['image'] = img_url
        if og_site_name:
            preview_data['site_name'] = og_site_name.get('content', '').strip()
        if og_type:
            preview_data['type'] = og_type.get('content', '').strip()
        
        # Fallback to Twitter cards
        if not preview_data['title']:
            twitter_title = soup.find('meta', attrs={'name': 'twitter:title'})
            if twitter_title:
                preview_data['title'] = twitter_title.get('content', '').strip()
        
        if not preview_data['description']:
            twitter_desc = soup.find('meta', attrs={'name': 'twitter:description'})
            if twitter_desc:
                preview_data['description'] = twitter_desc.get('content', '').strip()
        
        if not preview_data['image']:
            twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
            if twitter_image:
                img_url = twitter_image.get('content', '').strip()
                if img_url and not img_url.startswith(('http://', 'https://')):
                    img_url = urljoin(url, img_url)
                preview_data['image'] = img_url
        
        # Fallback to standard meta tags
        if not preview_data['title']:
            title_tag = soup.find('title')
            if title_tag:
                preview_data['title'] = title_tag.get_text().strip()
        
        if not preview_data['description']:
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc:
                preview_data['description'] = meta_desc.get('content', '').strip()
        
        # Get site name from domain if not found
        if not preview_data['site_name']:
            preview_data['site_name'] = parsed.netloc.replace('www.', '')
        
        # Truncate description if too long
        if preview_data['description'] and len(preview_data['description']) > 300:
            preview_data['description'] = preview_data['description'][:297] + '...'
        
        # Cache the result for 1 hour
        cache.set(cache_key, preview_data, 60 * 60)
        
        return Response(preview_data)
        
    except UnsafeUrlError as exc:
        # A redirect hop pointed somewhere internal, or the response was not HTML.
        logger.warning('Refused link preview mid-fetch for %s: %s', url, exc)
        return Response(
            {'error': 'Invalid or disallowed URL'},
            status=status.HTTP_400_BAD_REQUEST
        )
    except LinkPreviewFetchError as exc:
        logger.warning('Upstream error fetching link preview for %s: %s', url, exc)
        return Response(
            {'error': 'Failed to fetch URL'},
            status=status.HTTP_502_BAD_GATEWAY
        )
    except requests.exceptions.Timeout:
        logger.warning(f"Timeout fetching link preview for {url}")
        return Response(
            {'error': 'Request timeout'},
            status=status.HTTP_504_GATEWAY_TIMEOUT
        )
    except requests.exceptions.RequestException as e:
        logger.warning(f"Error fetching link preview for {url}: {e}")
        return Response(
            {'error': 'Failed to fetch URL'},
            status=status.HTTP_502_BAD_GATEWAY
        )
    except Exception as e:
        logger.error(f"Unexpected error fetching link preview for {url}: {e}")
        return Response(
            {'error': 'Internal server error'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def link_preview_image(request):
    """Serve a preview thumbnail from our own domain (MED-279).

    Hotlinking og:image meant every viewer's browser contacted the third-party
    host, handing it their IP. Proxying removes that, but only for images we have
    already recorded: an endpoint that fetched any URL a caller named would be a
    general-purpose SSRF tool, which is a worse hole than the one being closed.
    """
    url = request.query_params.get('url')
    if not url:
        return Response({'error': 'url is required'}, status=status.HTTP_400_BAD_REQUEST)

    # The allowlist is the cache itself: only images a guarded fetch already found.
    if not LinkPreview.objects.filter(
        image_url=url, status=LinkPreview.STATUS_READY
    ).exists():
        logger.warning('Refused to proxy an image that is not in the preview cache')
        return Response({'error': 'Unknown image'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        data, content_type = fetch_image_safely(url)
    except UnsafeUrlError as exc:
        chat_link_preview_refusals_total.labels(reason='image_guard').inc()
        logger.warning('Refused to proxy image: %s', exc)
        return Response({'error': 'Image unavailable'}, status=status.HTTP_400_BAD_REQUEST)
    except (LinkPreviewFetchError, requests.RequestException) as exc:
        logger.info('Image proxy upstream failure: %s', exc)
        return Response({'error': 'Image unavailable'}, status=status.HTTP_502_BAD_GATEWAY)

    response = HttpResponse(data, content_type=content_type)
    # Served from our origin, so be explicit that it is only ever an image.
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    response['Referrer-Policy'] = 'no-referrer'
    response['Cache-Control'] = 'private, max-age=3600'
    return response


# ── Full-text message search ───────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def search_messages(request):
    """
    GET /api/chat/search/

    Query params:
      q            str   required, min 2 chars
      from_user    str   optional — username/email substring
      in_chat      int   optional — chat id
      has          str   optional — 'file'
      date_after   str   optional — ISO date YYYY-MM-DD
      date_before  str   optional — ISO date YYYY-MM-DD
      limit        int   default 20, max 50
      offset       int   default 0
      cursor       str   optional — keyset cursor from previous response
    """
    from django.contrib.postgres.search import SearchQuery, SearchRank, SearchHeadline
    from .serializers import MessageSearchResultSerializer

    q = request.query_params.get('q', '').strip()
    has_filters = any([
        request.query_params.get('from_user', '').strip(),
        request.query_params.get('in_chat', '').strip(),
        request.query_params.get('has', '').strip(),
        request.query_params.get('date_after', '').strip(),
        request.query_params.get('date_before', '').strip(),
        request.query_params.get('threads_only', '').lower() in ('true', '1'),
        request.query_params.get('mentions_me', '').strip(),
    ])
    if len(q) < 2 and not has_filters:
        return Response({'results': [], 'total': 0, 'q': q})

    limit = min(int(request.query_params.get('limit', 20)), 50)
    offset = max(int(request.query_params.get('offset', 0)), 0)
    cursor = request.query_params.get('cursor', '').strip()
    decoded_cursor = _decode_search_cursor(cursor) if cursor else None
    if cursor and decoded_cursor is None:
        return Response({'error': 'Invalid cursor'}, status=status.HTTP_400_BAD_REQUEST)

    # Parse threads_only early — it changes the base queryset shape
    threads_only = request.query_params.get('threads_only', '').lower() in ('true', '1')

    # Base queryset — only chats the current user participates in.
    # When threads_only is active we must include thread replies too, so we
    # drop the parent_message__isnull=True restriction.
    base_qs_filter = dict(
        chat__participants__user=request.user,
        chat__participants__is_active=True,
        is_deleted=False,
        is_revoked=False,
    )
    if not threads_only:
        base_qs_filter['parent_message__isnull'] = True  # root messages only

    qs = Message.objects.filter(**base_qs_filter).exclude(
        hidden_by_users=request.user
    ).distinct()

    # Full-text search with icontains fallback — only when query is provided
    uses_rank_cursor = False
    if len(q) >= 2:
        try:
            sq = SearchQuery(q, search_type='websearch', config='english')
            qs = (
                qs
                .filter(search_vector=sq)
                .annotate(
                    rank=SearchRank('search_vector', sq),
                    highlight=SearchHeadline(
                        'content', sq,
                        config='english',
                        options='MaxFragments=1,MaxWords=15,MinWords=5,StartSel=<mark>,StopSel=</mark>',
                    ),
                )
                .order_by('-rank', '-created_at', '-id')
            )
            uses_rank_cursor = True
        except Exception as exc:
            # Fallback: icontains (e.g. search_vector not yet populated)
            logger.warning(
                "chat.search_fts_fallback",
                extra={
                    "user_id": request.user.id,
                    "query_length": len(q),
                    "exception_type": exc.__class__.__name__,
                },
            )
            qs = qs.filter(content__icontains=q).order_by('-created_at', '-id')
    else:
        # Filter-only search — no text constraint, order by recency
        qs = qs.order_by('-created_at', '-id')

    # Optional filters
    from_user = request.query_params.get('from_user', '').strip()
    if from_user:
        qs = qs.filter(
            Q(sender__username__icontains=from_user) |
            Q(sender__email__icontains=from_user)
        )

    in_chat = request.query_params.get('in_chat', '').strip()
    if in_chat and in_chat.isdigit():
        qs = qs.filter(chat_id=int(in_chat))

    has = request.query_params.get('has', '').strip()
    if has == 'file':
        qs = qs.filter(has_attachments=True)
    elif has == 'link':
        qs = qs.filter(content__iregex=r'https?://')

    # threads_only: include root messages that have replies AND the replies themselves
    if threads_only:
        qs = qs.filter(
            Q(thread_replies__isnull=False) | Q(parent_message__isnull=False)
        ).distinct()

    mentions_me = request.query_params.get('mentions_me', '').strip()
    if mentions_me:
        qs = qs.filter(content__icontains=f'@{mentions_me}')

    date_after = request.query_params.get('date_after', '').strip()
    if date_after:
        qs = qs.filter(created_at__date__gte=date_after)

    date_before = request.query_params.get('date_before', '').strip()
    if date_before:
        qs = qs.filter(created_at__date__lte=date_before)

    qs = qs.select_related('sender', 'chat', 'chat__project').prefetch_related(
        'attachments',
        Prefetch(
            'chat__participants',
            queryset=ChatParticipant.objects.filter(is_active=True).select_related('user'),
        ),
    )

    total = qs.count()
    if decoded_cursor:
        created_at = decoded_cursor['created_at']
        message_id = decoded_cursor['id']
        if uses_rank_cursor:
            rank = decoded_cursor.get('rank') or 0
            qs = qs.filter(
                Q(rank__lt=rank) |
                Q(rank=rank, created_at__lt=created_at) |
                Q(rank=rank, created_at=created_at, id__lt=message_id)
            )
        else:
            qs = qs.filter(
                Q(created_at__lt=created_at) |
                Q(created_at=created_at, id__lt=message_id)
            )
        page_rows = list(qs[:limit + 1])
    else:
        page_rows = list(qs[offset: offset + limit + 1])

    has_next = len(page_rows) > limit
    page = page_rows[:limit]
    next_cursor = _encode_search_cursor(page[-1], include_rank=uses_rank_cursor) if has_next and page else None

    serializer = MessageSearchResultSerializer(page, many=True, context={'request': request})
    return Response({'results': serializer.data, 'total': total, 'q': q, 'next_cursor': next_cursor})

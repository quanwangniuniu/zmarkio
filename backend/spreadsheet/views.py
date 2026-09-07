from core.slug_mixins import resolve_lookup_kwargs, resolve_project_pk
"""
API views for spreadsheet operations
Handles CRUD operations for spreadsheets, sheets, rows, columns, and cells
"""
import logging
import re
import time
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError, NotFound
from rest_framework.throttling import ScopedRateThrottle
from django.shortcuts import get_object_or_404
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse

from .xlsx_export import build_sheet_workbook

from .models import (
    Spreadsheet,
    Sheet,
    SheetRow,
    SheetColumn,
    Cell,
    CellValueType,
    WorkflowPattern,
    PatternJob,
    PatternJobStatus,
    SpreadsheetHighlight,
    SpreadsheetCellFormat,
    SpreadsheetHighlightScope,
    PivotConfig,
    SheetKind,
)
from .serializers import (
    SpreadsheetSerializer,
    SpreadsheetCreateSerializer,
    SpreadsheetUpdateSerializer,
    SheetSerializer,
    SheetCreateSerializer,
    SheetUpdateSerializer,
    SheetRowSerializer,
    SheetColumnSerializer,
    SheetResizeSerializer,
    SheetResizeResponseSerializer,
    CellRangeReadSerializer,
    CellRangeResponseSerializer,
    CellSerializer,
    SheetInsertSerializer,
    SheetDeleteSerializer,
    CellBatchUpdateSerializer,
    CellBatchUpdateResponseSerializer,
    SheetSortSerializer,
    SheetReorderSerializer,
    WorkflowPatternCreateSerializer,
    WorkflowPatternListSerializer,
    WorkflowPatternDetailSerializer,
    PatternApplySerializer,
    PatternJobStatusSerializer,
    SpreadsheetHighlightSerializer,
    SpreadsheetHighlightBatchSerializer,
    SpreadsheetCellFormatSerializer,
    SpreadsheetCellFormatBatchSerializer,
    PivotConfigSerializer,
    PivotConfigCreateUpdateSerializer,
)
from .services import (
    SpreadsheetService, SheetService, CellService, CellBatchArgumentError,
    broadcast_sheet_refresh, broadcast_spreadsheet_sheet_list_refresh,
)
from .models import SheetStructureOperation
from .exceptions import SheetRevisionConflict
from .ws_tickets import WS_TICKET_TTL_SECONDS, mint_websocket_ticket
from .tenant import current_tenant_schema
from .tasks import apply_pattern_job
from .access import (
    get_accessible_project_or_404,
    get_accessible_sheet_or_404,
    get_accessible_spreadsheet_or_404,
)

logger = logging.getLogger(__name__)

# Header carrying the editing tab's collab WS client id (echo suppression).
SHEET_CLIENT_ID_HEADER = 'X-Sheet-Client-Id'


def _request_sheet_client_id(request, fallback=None):
    """Return the validated collab client id shared by HTTP and WS paths."""
    value = (request.headers.get(SHEET_CLIENT_ID_HEADER) or fallback or '').strip()
    if len(value) > 64:
        raise ValidationError({
            'client_id': 'Client id must be 64 characters or fewer.',
        })
    return value or None


def _request_base_revision(request):
    value = request.data.get('base_revision') if isinstance(request.data, dict) else None
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValidationError({'base_revision': 'Must be a non-negative integer.'})
    try:
        revision = int(value)
    except (TypeError, ValueError):
        raise ValidationError({'base_revision': 'Must be a non-negative integer.'})
    if revision < 0:
        raise ValidationError({'base_revision': 'Must be a non-negative integer.'})
    return revision


def _request_token_expiry(request):
    now = int(time.time())
    hard_expiry = now + int(
        getattr(settings, 'SPREADSHEET_WS_CONNECTION_MAX_SECONDS', 3600)
    )
    token = getattr(request, 'auth', None)
    try:
        expiry = int(token.get('exp'))
        if expiry > now:
            return min(expiry, hard_expiry)
    except (AttributeError, TypeError, ValueError):
        pass
    return hard_expiry


def _lock_and_validate_sheet_revision(sheet, base_revision):
    """Lock a coordinate-write domain and reject obsolete coordinate requests."""
    locked_sheet = Sheet.objects.select_for_update().get(pk=sheet.pk)
    if base_revision is not None and base_revision != locked_sheet.revision:
        raise SheetRevisionConflict(base_revision, locked_sheet.revision)
    return locked_sheet


def _notify_sheet_refresh(request, sheet, reason: str) -> None:
    """Broadcast sheet_refresh_required after a successful structure mutation."""
    sheet.refresh_from_db(fields=['revision'])
    broadcast_sheet_refresh(
        sheet_id=sheet.id,
        reason=reason,
        origin_client_id=_request_sheet_client_id(request),
        origin_user_id=getattr(request.user, 'id', None),
        revision=sheet.revision,
    )


def _notify_spreadsheet_sheet_list_refresh(
    request,
    spreadsheet,
    reason: str,
    include_sheet_ids=None,
) -> None:
    """Broadcast a sheet-tab list change to every room in a spreadsheet."""
    broadcast_spreadsheet_sheet_list_refresh(
        spreadsheet_id=spreadsheet.id,
        reason=reason,
        include_sheet_ids=include_sheet_ids,
        origin_client_id=_request_sheet_client_id(request),
        origin_user_id=getattr(request.user, 'id', None),
    )


class SpreadsheetListView(APIView):
    """List and create spreadsheets"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """
        List spreadsheets for a project
        GET /spreadsheets/?project_id=1&page=1&page_size=20&search=name&order_by=created_at
        """
        project_id = request.query_params.get('project_id')
        if not project_id:
            raise ValidationError({'project_id': 'project_id is required'})
        
        # project_id query param tolerates a numeric pk OR a project slug; the
        # frontend sends the slug, so resolving to a pk first avoids a spurious 404.
        project = get_accessible_project_or_404(
            request.user,
            pk=resolve_project_pk(project_id),
        )

        # Get queryset with select_related to avoid N+1 queries
        queryset = Spreadsheet.objects.filter(project=project, is_deleted=False).select_related('project')
        
        # Search by name
        search = request.query_params.get('search')
        if search:
            queryset = queryset.filter(name__icontains=search)
        
        # Order by
        order_by = request.query_params.get('order_by', 'created_at')
        if order_by in ['name', 'created_at', 'updated_at']:
            queryset = queryset.order_by(f'-{order_by}' if order_by != 'name' else 'name')
        else:
            queryset = queryset.order_by('-created_at')
        
        # Pagination
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        
        paginator = Paginator(queryset, page_size)
        page_obj = paginator.get_page(page)
        
        serializer = SpreadsheetSerializer(page_obj, many=True)
        
        return Response({
            'count': paginator.count,
            'page': page,
            'page_size': page_size,
            'results': serializer.data
        })
    
    def post(self, request):
        """
        Create a new spreadsheet
        POST /spreadsheets/?project_id=1
        """
        project_id = request.query_params.get('project_id')
        if not project_id:
            raise ValidationError({'project_id': 'project_id is required'})
        
        # project_id query param tolerates a numeric pk OR a project slug.
        project = get_accessible_project_or_404(
            request.user,
            pk=resolve_project_pk(project_id),
        )
        
        serializer = SpreadsheetCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            spreadsheet = SpreadsheetService.create_spreadsheet(
                project=project,
                name=serializer.validated_data['name']
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})
        
        # Refetch with select_related to ensure project is loaded for serialization
        spreadsheet = Spreadsheet.objects.select_related('project').get(id=spreadsheet.id)
        response_serializer = SpreadsheetSerializer(spreadsheet)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class SpreadsheetDetailView(APIView):
    """Retrieve, update, and delete a spreadsheet"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request, id):
        """Get spreadsheet details"""
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user,
            **resolve_lookup_kwargs(id),
        )
        serializer = SpreadsheetSerializer(spreadsheet)
        return Response(serializer.data)
    
    def put(self, request, id):
        """Update spreadsheet"""
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user,
            **resolve_lookup_kwargs(id),
        )
        
        serializer = SpreadsheetUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            updated_spreadsheet = SpreadsheetService.update_spreadsheet(
                spreadsheet=spreadsheet,
                name=serializer.validated_data['name']
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})
        
        # Refetch with select_related to ensure project is loaded for serialization
        updated_spreadsheet = Spreadsheet.objects.select_related('project').get(id=updated_spreadsheet.id)
        response_serializer = SpreadsheetSerializer(updated_spreadsheet)
        return Response(response_serializer.data)
    
    def delete(self, request, id):
        """Delete spreadsheet (soft delete)"""
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user,
            **resolve_lookup_kwargs(id),
        )
        SpreadsheetService.delete_spreadsheet(spreadsheet)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkflowPatternListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        patterns = WorkflowPattern.objects.filter(owner=request.user, is_archived=False)
        serializer = WorkflowPatternListSerializer(patterns, many=True)
        return Response({'results': serializer.data})

    def post(self, request):
        serializer = WorkflowPatternCreateSerializer(data=request.data, context={'owner': request.user})
        serializer.is_valid(raise_exception=True)
        pattern = serializer.save()
        response_serializer = WorkflowPatternDetailSerializer(pattern)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class WorkflowPatternDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, id):
        pattern = get_object_or_404(WorkflowPattern, id=id, owner=request.user)
        serializer = WorkflowPatternDetailSerializer(pattern)
        return Response(serializer.data)

    def delete(self, request, id):
        pattern = get_object_or_404(WorkflowPattern, id=id, owner=request.user)
        pattern.is_archived = True
        pattern.save(update_fields=['is_archived', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkflowPatternApplyView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, id):
        pattern = get_object_or_404(WorkflowPattern, id=id, owner=request.user, is_archived=False)
        serializer = PatternApplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        spreadsheet_id = serializer.validated_data['spreadsheet_id']
        sheet_id = serializer.validated_data['sheet_id']

        # spreadsheet_id comes from the request body (numeric FK contract), not the URL
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user,
            id=spreadsheet_id,
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)

        job = PatternJob.objects.create(
            pattern=pattern,
            spreadsheet=spreadsheet,
            sheet=sheet,
            status=PatternJobStatus.QUEUED,
            progress=0,
            created_by=request.user
        )
        try:
            apply_pattern_job.delay(str(job.id))
            logger.info(
                "Enqueued pattern apply job %s via broker %s",
                job.id,
                settings.CELERY_BROKER_URL
            )
        except Exception:
            logger.exception(
                "Failed to enqueue pattern apply job %s via broker %s",
                job.id,
                settings.CELERY_BROKER_URL
            )
            return Response(
                {'error': 'Failed to enqueue pattern apply job'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        return Response(
            {'job_id': str(job.id), 'status': job.status},
            status=status.HTTP_202_ACCEPTED
        )


class PatternJobStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id):
        job = get_object_or_404(PatternJob, id=job_id, created_by=request.user)
        serializer = PatternJobStatusSerializer(job)
        return Response(serializer.data)


class SheetListView(APIView):
    """List and create sheets"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request, spreadsheet_id):
        """
        List sheets for a spreadsheet
        GET /spreadsheets/{spreadsheet_id}/sheets/?page=1&page_size=20&order_by=position
        """
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user,
            **resolve_lookup_kwargs(spreadsheet_id),
        )
        
        # Use select_related to avoid N+1 queries when accessing spreadsheet.id in serializer
        queryset = Sheet.objects.filter(spreadsheet=spreadsheet, is_deleted=False).select_related('spreadsheet')
        
        # Order by
        order_by = request.query_params.get('order_by', 'position')
        if order_by in ['name', 'position', 'created_at']:
            if order_by == 'name':
                queryset = queryset.order_by('name')
            elif order_by == 'position':
                queryset = queryset.order_by('position', 'created_at')
            else:
                queryset = queryset.order_by('-created_at')
        else:
            queryset = queryset.order_by('position', 'created_at')
        
        # Pagination
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        
        paginator = Paginator(queryset, page_size)
        page_obj = paginator.get_page(page)
        
        serializer = SheetSerializer(page_obj, many=True)
        
        return Response({
            'count': paginator.count,
            'page': page,
            'page_size': page_size,
            'results': serializer.data
        })
    
    def post(self, request, spreadsheet_id):
        """
        Create a new sheet
        POST /spreadsheets/{spreadsheet_id}/sheets/
        """
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user,
            **resolve_lookup_kwargs(spreadsheet_id),
        )
        
        serializer = SheetCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            sheet = SheetService.create_sheet(
                spreadsheet=spreadsheet,
                name=serializer.validated_data['name']
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})
        
        # Refetch with select_related to ensure spreadsheet is loaded for serialization
        sheet = Sheet.objects.select_related('spreadsheet').get(id=sheet.id)
        _notify_spreadsheet_sheet_list_refresh(request, spreadsheet, 'sheet_created')
        response_serializer = SheetSerializer(sheet)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class SheetDetailView(APIView):
    """Retrieve, update, and delete a sheet"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request, spreadsheet_id, id):
        """Get sheet details"""
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user,
            **resolve_lookup_kwargs(spreadsheet_id),
        )
        sheet = get_object_or_404(
            Sheet.objects.select_related('spreadsheet'),
            id=id,
            spreadsheet=spreadsheet,
            is_deleted=False
        )
        serializer = SheetSerializer(sheet)
        return Response(serializer.data)
    
    def put(self, request, spreadsheet_id, id):
        """Update sheet"""
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user,
            **resolve_lookup_kwargs(spreadsheet_id),
        )
        sheet = get_object_or_404(
            Sheet.objects.select_related('spreadsheet'),
            id=id,
            spreadsheet=spreadsheet,
            is_deleted=False
        )
        
        serializer = SheetUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        
        try:
            updated_sheet = SheetService.update_sheet(
                sheet=sheet,
                name=data.get('name', sheet.name),
                frozen_row_count=data.get('frozen_row_count'),
                frozen_column_count=data.get('frozen_column_count'),
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})
        
        # Refetch with select_related to ensure spreadsheet is loaded for serialization
        updated_sheet = Sheet.objects.select_related('spreadsheet').get(id=updated_sheet.id)
        _notify_spreadsheet_sheet_list_refresh(request, spreadsheet, 'sheet_updated')
        response_serializer = SheetSerializer(updated_sheet)
        return Response(response_serializer.data)
    
    def delete(self, request, spreadsheet_id, id):
        """Delete sheet (soft delete)"""
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user,
            **resolve_lookup_kwargs(spreadsheet_id),
        )
        sheet = get_object_or_404(
            Sheet.objects.select_related('spreadsheet'),
            id=id,
            spreadsheet=spreadsheet,
            is_deleted=False
        )
        SheetService.delete_sheet(sheet)
        _notify_spreadsheet_sheet_list_refresh(
            request,
            spreadsheet,
            'sheet_deleted',
            include_sheet_ids=[sheet.id],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProjectSheetDeleteView(APIView):
    """
    Delete a sheet via project-scoped route:
    DELETE /api/projects/{project_id}/spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, project_id, spreadsheet_id, sheet_id):
        project = get_accessible_project_or_404(
            request.user,
            **resolve_lookup_kwargs(project_id, 'id'),
        )
        spreadsheet = get_object_or_404(
            Spreadsheet,
            **resolve_lookup_kwargs(spreadsheet_id, 'id'),
            project=project,
            is_deleted=False
        )
        sheet = get_object_or_404(
            Sheet.objects.select_related('spreadsheet'),
            id=sheet_id,
            spreadsheet=spreadsheet,
            is_deleted=False
        )
        SheetService.delete_sheet(sheet)
        _notify_spreadsheet_sheet_list_refresh(
            request,
            spreadsheet,
            'sheet_deleted',
            include_sheet_ids=[sheet.id],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SheetWebSocketTicketView(APIView):
    """Mint a short-lived, one-time credential bound to one sheet and tab."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'spreadsheet_ws_ticket'

    def post(self, request, sheet_id):
        sheet = get_accessible_sheet_or_404(
            request.user,
            id=sheet_id,
        )
        client_id = _request_sheet_client_id(
            request,
            request.data.get('client_id'),
        )
        if not client_id:
            raise ValidationError({'client_id': 'This field is required.'})
        ticket = mint_websocket_ticket(
            user_id=request.user.id,
            sheet_id=sheet.id,
            client_id=client_id,
            connection_expires_at=_request_token_expiry(request),
            tenant_schema=current_tenant_schema(),
        )
        return Response({
            'ticket': ticket,
            'expires_in': WS_TICKET_TTL_SECONDS,
        })


class SheetResizeView(APIView):
    """Resize a sheet (create rows/columns)"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request, spreadsheet_id, sheet_id):
        """
        Resize sheet to ensure it has at least the specified number of rows/columns
        POST /spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/resize
        """
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)
        
        serializer = SheetResizeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            result = SheetService.resize_sheet(
                sheet=sheet,
                row_count=serializer.validated_data['row_count'],
                column_count=serializer.validated_data['column_count'],
                base_revision=serializer.validated_data.get('base_revision'),
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})
        
        _notify_sheet_refresh(request, sheet, 'resize')
        response_serializer = SheetResizeResponseSerializer(result)
        return Response(response_serializer.data)


class SheetSortView(APIView):
    """Sort rows by column (updates SheetRow.position only, no cell rewrite)"""
    permission_classes = [IsAuthenticated]

    def post(self, request, spreadsheet_id, sheet_id):
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)

        serializer = SheetSortSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = SheetService.sort_rows(
                sheet=sheet,
                column_position=serializer.validated_data['column_position'],
                direction=serializer.validated_data['direction'],
                has_header=serializer.validated_data['has_header'],
                previous_sort_columns=serializer.validated_data.get('previous_sort_columns') or [],
                base_revision=serializer.validated_data.get('base_revision'),
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})

        _notify_sheet_refresh(request, sheet, 'sort')
        return Response(result)


class SheetReorderView(APIView):
    """Reorder rows by position mapping (for undo/redo)"""
    permission_classes = [IsAuthenticated]

    def post(self, request, spreadsheet_id, sheet_id):
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)

        serializer = SheetReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            revision = SheetService.reorder_rows(
                sheet=sheet,
                order=serializer.validated_data['order'],
                base_revision=serializer.validated_data.get('base_revision'),
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})

        _notify_sheet_refresh(request, sheet, 'reorder')
        return Response({'status': 'ok', 'revision': revision})


class SheetRowListView(APIView):
    """List rows in a sheet (scrollable)"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request, spreadsheet_id, sheet_id):
        """
        List rows in a sheet using scrollable pagination
        GET /spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/rows/?offset=0&row_limit=100
        """
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)
        
        offset = int(request.query_params.get('offset', 0))
        row_limit = int(request.query_params.get('row_limit', 100))
        
        # Clamp row_limit to max 500
        row_limit = min(row_limit, 500)
        
        # Use select_related to avoid N+1 queries when accessing sheet.id in serializer
        queryset = SheetRow.objects.filter(sheet=sheet, is_deleted=False).select_related('sheet').order_by('position')
        
        total = queryset.count()
        items = queryset[offset:offset + row_limit]
        has_more = (offset + row_limit) < total
        
        serializer = SheetRowSerializer(items, many=True)
        
        return Response({
            'items': serializer.data,
            'offset': offset,
            'limit': row_limit,
            'total': total,
            'has_more': has_more
        })


class SheetColumnListView(APIView):
    """List columns in a sheet (scrollable)"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request, spreadsheet_id, sheet_id):
        """
        List columns in a sheet using scrollable pagination
        GET /spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/columns/?offset=0&column_limit=50
        """
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)
        
        offset = int(request.query_params.get('offset', 0))
        column_limit = int(request.query_params.get('column_limit', 50))
        
        # Clamp column_limit to max 200
        column_limit = min(column_limit, 200)
        
        # Use select_related to avoid N+1 queries when accessing sheet.id in serializer
        queryset = SheetColumn.objects.filter(sheet=sheet, is_deleted=False).select_related('sheet').order_by('position')
        
        total = queryset.count()
        items = queryset[offset:offset + column_limit]
        has_more = (offset + column_limit) < total
        
        serializer = SheetColumnSerializer(items, many=True)
        
        return Response({
            'items': serializer.data,
            'offset': offset,
            'limit': column_limit,
            'total': total,
            'has_more': has_more
        })


class SheetRowInsertView(APIView):
    """Insert rows in a sheet"""
    permission_classes = [IsAuthenticated]

    def post(self, request, spreadsheet_id, sheet_id):
        """
        Insert rows at a position
        POST /spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/rows/insert
        """
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)

        serializer = SheetInsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = SheetService.insert_rows(
                sheet=sheet,
                position=serializer.validated_data['position'],
                count=serializer.validated_data.get('count', 1),
                created_by=request.user,
                base_revision=serializer.validated_data.get('base_revision'),
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})

        _notify_sheet_refresh(request, sheet, 'rows_inserted')
        return Response(result, status=status.HTTP_201_CREATED)


class SheetColumnInsertView(APIView):
    """Insert columns in a sheet"""
    permission_classes = [IsAuthenticated]

    def post(self, request, spreadsheet_id, sheet_id):
        """
        Insert columns at a position
        POST /spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/columns/insert
        """
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)

        serializer = SheetInsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = SheetService.insert_columns(
                sheet=sheet,
                position=serializer.validated_data['position'],
                count=serializer.validated_data.get('count', 1),
                created_by=request.user,
                base_revision=serializer.validated_data.get('base_revision'),
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})

        _notify_sheet_refresh(request, sheet, 'columns_inserted')
        return Response(result, status=status.HTTP_201_CREATED)


class SheetRowDeleteView(APIView):
    """Delete rows in a sheet"""
    permission_classes = [IsAuthenticated]

    def post(self, request, spreadsheet_id, sheet_id):
        """
        Delete rows at a position
        POST /spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/rows/delete
        """
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)

        serializer = SheetDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = SheetService.delete_rows(
                sheet=sheet,
                position=serializer.validated_data['position'],
                count=serializer.validated_data.get('count', 1),
                created_by=request.user,
                base_revision=serializer.validated_data.get('base_revision'),
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})

        _notify_sheet_refresh(request, sheet, 'rows_deleted')
        return Response(result, status=status.HTTP_200_OK)


class SheetColumnDeleteView(APIView):
    """Delete columns in a sheet"""
    permission_classes = [IsAuthenticated]

    def post(self, request, spreadsheet_id, sheet_id):
        """
        Delete columns at a position
        POST /spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/columns/delete
        """
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)

        serializer = SheetDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = SheetService.delete_columns(
                sheet=sheet,
                position=serializer.validated_data['position'],
                count=serializer.validated_data.get('count', 1),
                created_by=request.user,
                base_revision=serializer.validated_data.get('base_revision'),
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})

        _notify_sheet_refresh(request, sheet, 'columns_deleted')
        return Response(result, status=status.HTTP_200_OK)


class SheetStructureOperationRevertView(APIView):
    """Revert a structure operation"""
    permission_classes = [IsAuthenticated]

    def post(self, request, spreadsheet_id, sheet_id, operation_id):
        """
        Revert a structure operation
        POST /spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/operations/{operation_id}/revert
        """
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)
        operation = get_object_or_404(
            SheetStructureOperation, id=operation_id, sheet=sheet
        )

        try:
            result = SheetService.revert_structure_operation(
                sheet=sheet,
                operation=operation,
                base_revision=_request_base_revision(request),
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})

        _notify_sheet_refresh(request, sheet, 'operation_reverted')
        return Response(result, status=status.HTTP_200_OK)


class SheetRowInsertByIdView(APIView):
    """Insert rows using sheet_id-only endpoint"""
    permission_classes = [IsAuthenticated]

    def post(self, request, sheet_id):
        sheet = get_accessible_sheet_or_404(request.user, id=sheet_id)
        serializer = SheetInsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = SheetService.insert_rows(
                sheet=sheet,
                position=serializer.validated_data['position'],
                count=serializer.validated_data.get('count', 1),
                created_by=request.user,
                base_revision=serializer.validated_data.get('base_revision'),
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})

        _notify_sheet_refresh(request, sheet, 'rows_inserted')
        return Response(result, status=status.HTTP_201_CREATED)


class SheetColumnInsertByIdView(APIView):
    """Insert columns using sheet_id-only endpoint"""
    permission_classes = [IsAuthenticated]

    def post(self, request, sheet_id):
        sheet = get_accessible_sheet_or_404(request.user, id=sheet_id)
        serializer = SheetInsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = SheetService.insert_columns(
                sheet=sheet,
                position=serializer.validated_data['position'],
                count=serializer.validated_data.get('count', 1),
                created_by=request.user,
                base_revision=serializer.validated_data.get('base_revision'),
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})

        _notify_sheet_refresh(request, sheet, 'columns_inserted')
        return Response(result, status=status.HTTP_201_CREATED)


class SheetRowDeleteByIdView(APIView):
    """Delete rows using sheet_id-only endpoint"""
    permission_classes = [IsAuthenticated]

    def post(self, request, sheet_id):
        sheet = get_accessible_sheet_or_404(request.user, id=sheet_id)
        serializer = SheetDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = SheetService.delete_rows(
                sheet=sheet,
                position=serializer.validated_data['position'],
                count=serializer.validated_data.get('count', 1),
                created_by=request.user,
                base_revision=serializer.validated_data.get('base_revision'),
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})

        _notify_sheet_refresh(request, sheet, 'rows_deleted')
        return Response(result, status=status.HTTP_200_OK)


class SheetColumnDeleteByIdView(APIView):
    """Delete columns using sheet_id-only endpoint"""
    permission_classes = [IsAuthenticated]

    def post(self, request, sheet_id):
        sheet = get_accessible_sheet_or_404(request.user, id=sheet_id)
        serializer = SheetDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = SheetService.delete_columns(
                sheet=sheet,
                position=serializer.validated_data['position'],
                count=serializer.validated_data.get('count', 1),
                created_by=request.user,
                base_revision=serializer.validated_data.get('base_revision'),
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})

        _notify_sheet_refresh(request, sheet, 'columns_deleted')
        return Response(result, status=status.HTTP_200_OK)


class SheetOperationRevertByIdView(APIView):
    """Revert a structure operation using sheet_id-only endpoint"""
    permission_classes = [IsAuthenticated]

    def post(self, request, sheet_id, operation_id):
        sheet = get_accessible_sheet_or_404(request.user, id=sheet_id)
        operation = get_object_or_404(
            SheetStructureOperation, id=operation_id, sheet=sheet
        )

        try:
            result = SheetService.revert_structure_operation(
                sheet=sheet,
                operation=operation,
                base_revision=_request_base_revision(request),
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})

        _notify_sheet_refresh(request, sheet, 'operation_reverted')
        return Response(result, status=status.HTTP_200_OK)


class CellRangeReadView(APIView):
    """Read cells within a range"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request, spreadsheet_id, sheet_id):
        """
        Read cells within a specified range
        POST /spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/cells/range
        """
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)
        
        serializer = CellRangeReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            result = CellService.read_cell_range(
                sheet=sheet,
                start_row=serializer.validated_data['start_row'],
                end_row=serializer.validated_data['end_row'],
                start_column=serializer.validated_data['start_column'],
                end_column=serializer.validated_data['end_column'],
                include_sheet_dimensions=serializer.validated_data.get(
                    'include_sheet_dimensions', True
                ),
            )
        except DjangoValidationError as e:
            raise ValidationError({'error': str(e)})
        
        # Serialize cells
        cell_serializer = CellSerializer(result['cells'], many=True)
        
        return Response({
            'cells': cell_serializer.data,
            'row_count': result['row_count'],
            'column_count': result['column_count'],
            'sheet_row_count': result.get('sheet_row_count'),
            'sheet_column_count': result.get('sheet_column_count'),
            'revision': result['revision'],
        })


class CellBatchUpdateView(APIView):
    """Batch update cells"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request, spreadsheet_id, sheet_id):
        """
        Perform batch cell operations (set or clear)
        POST /spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/cells/batch
        """
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)

        serializer = CellBatchUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning(
                "CellBatchUpdateSerializer validation failed sheet_id=%s ops_count=%s errors=%s",
                sheet_id,
                len(request.data.get('operations', [])) if isinstance(request.data, dict) else '?',
                serializer.errors,
            )
            raise ValidationError(serializer.errors)
        
        import_id = serializer.validated_data.get('import_id')
        chunk_index = serializer.validated_data.get('chunk_index')
        import_mode = serializer.validated_data.get('import_mode', False)
        if import_id is not None or chunk_index is not None:
            logger.info(
                "Cell batch import chunk sheet_id=%s import_id=%s chunk_index=%s",
                sheet_id, import_id, chunk_index
            )
        
        try:
            result = CellService.batch_update_cells(
                sheet=sheet,
                operations=serializer.validated_data['operations'],
                auto_expand=serializer.validated_data.get('auto_expand', True),
                import_mode=import_mode,
                origin_client_id=_request_sheet_client_id(
                    request,
                    serializer.validated_data.get('client_id'),
                ),
                origin_user_id=getattr(request.user, 'id', None),
                base_revision=serializer.validated_data.get('base_revision'),
            )
        except CellBatchArgumentError as e:
            return Response(
                {'code': e.code, 'details': e.details},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except DjangoValidationError as e:
            # Django's ValidationError has message_dict / messages / str(); it does NOT have .detail
            # (that attribute is on rest_framework.exceptions.ValidationError only).
            logger.warning(
                "Cell batch update validation failed: %s",
                getattr(e, 'message_dict', None) or getattr(e, 'messages', None) or str(e),
                exc_info=True,
            )
            # Build a JSON-serializable detail for 400 response.
            # Service raises ValidationError({'code': 'INVALID_ARGUMENT', 'details': [list of {index, row, column, field, message}]}).
            # Django stores that as message_dict; do not double-wrap (details must be that list, not message_dict).
            if hasattr(e, 'message_dict') and e.message_dict and 'code' in e.message_dict and 'details' in e.message_dict:
                code_val = e.message_dict['code']
                details_val = e.message_dict['details']
                code = code_val[0] if isinstance(code_val, list) else code_val
                details = details_val  # already the list of {index, row, column, field, message}
                detail = {'code': code, 'details': details}
            elif hasattr(e, 'message_dict') and e.message_dict:
                detail = {'code': 'INVALID_ARGUMENT', 'details': e.message_dict}
            elif hasattr(e, 'messages') and e.messages:
                detail = {'code': 'INVALID_ARGUMENT', 'details': list(e.messages)}
            else:
                detail = {
                    'code': 'INVALID_ARGUMENT',
                    'details': [{'field': 'general', 'message': str(e)}],
                }
            raise ValidationError(detail)

        response_serializer = CellBatchUpdateResponseSerializer(result)
        return Response(response_serializer.data)


class ImportFinalizeView(APIView):
    """Finalize import: recompute formulas and update sheet meta after all batch chunks."""
    permission_classes = [IsAuthenticated]

    def post(self, request, spreadsheet_id, sheet_id):
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)
        import_id = request.data.get('import_id')
        if import_id:
            logger.info("Import finalize sheet_id=%s import_id=%s", sheet_id, import_id)
        try:
            CellService.recalculate_sheet_formulas(sheet)
        except Exception as e:
            logger.exception("Import finalize recalc failed: %s", e)
            raise ValidationError({'detail': 'Formula recalculation failed'})
        # After finalize, run pivot recompute once for the whole import. batch_update_cells
        # skips this under import_mode to avoid N recomputes (one per chunk).
        try:
            from .pivot_service import recompute_pivots_for_source_sheet
            recompute_pivots_for_source_sheet(sheet)
        except Exception:
            logger.exception("Import finalize pivot recompute failed for sheet_id=%s", sheet.id)
        # Peers get one coarse refresh for the whole import (per-chunk broadcasts
        # are suppressed by import_mode in batch_update_cells).
        _notify_sheet_refresh(request, sheet, 'import_finalized')
        return Response({'status': 'ok', 'revision': sheet.revision})


class PivotConfigView(APIView):
    """
    Create/update and read pivot configuration for a pivot sheet.
    POST: upsert config only (metadata).
    GET:  return current config (or 404 if none).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, spreadsheet_id, sheet_id):
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)
        try:
            config = sheet.pivot_config
        except PivotConfig.DoesNotExist:
            raise NotFound({'error': 'Pivot config not found'})
        serializer = PivotConfigSerializer(config)
        return Response(serializer.data)

    def post(self, request, spreadsheet_id, sheet_id):
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)

        serializer = PivotConfigCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        source_sheet = get_object_or_404(
            Sheet,
            id=data['source_sheet_id'],
            spreadsheet=spreadsheet,
            is_deleted=False,
        )

        config, _created = PivotConfig.objects.update_or_create(
            pivot_sheet=sheet,
            defaults={
                'source_sheet': source_sheet,
                'rows_config': data.get('rows_config') or [],
                'columns_config': data.get('columns_config') or [],
                'values_config': data.get('values_config') or [],
                'filters_config': data.get('filters_config') or {},
                'show_grand_total_row': data.get('show_grand_total_row', True),
                'show_grand_total_column': data.get('show_grand_total_column', True),
            },
        )

        if sheet.kind != SheetKind.PIVOT:
            sheet.kind = SheetKind.PIVOT
            sheet.save(update_fields=['kind', 'updated_at'])

        response_serializer = PivotConfigSerializer(config)
        return Response(response_serializer.data)


class PivotRecomputeView(APIView):
    """
    Trigger pivot recomputation for a pivot sheet based on persisted config.
    Used as a fire-and-forget endpoint from the frontend; may be slow.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, spreadsheet_id, sheet_id):
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)
        try:
            config = sheet.pivot_config
        except PivotConfig.DoesNotExist:
            raise NotFound({'error': 'Pivot config not found'})

        from .pivot_service import recompute_pivot

        try:
            recompute_pivot(config)
        except Exception as e:
            logger.exception(
                "Pivot recompute failed via API for sheet_id=%s config_id=%s: %s",
                sheet.id,
                config.id,
                e,
            )
            # Return 202 even on failure; frontend treats this as best-effort background recompute.
            return Response({'status': 'error', 'detail': 'pivot recompute failed'}, status=status.HTTP_202_ACCEPTED)

        sheet.refresh_from_db(fields=['revision'])
        return Response(
            {'status': 'ok', 'revision': sheet.revision},
            status=status.HTTP_202_ACCEPTED,
        )


class SpreadsheetHighlightListView(APIView):
    """List highlights for a sheet"""
    permission_classes = [IsAuthenticated]

    def get(self, request, spreadsheet_id, sheet_id):
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)

        highlights = SpreadsheetHighlight.objects.filter(sheet=sheet).order_by('id')
        serializer = SpreadsheetHighlightSerializer(highlights, many=True)
        return Response({'highlights': serializer.data, 'revision': sheet.revision})


class SpreadsheetHighlightBatchView(APIView):
    """Batch set/clear highlights"""
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, spreadsheet_id, sheet_id):
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)

        serializer = SpreadsheetHighlightBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sheet = _lock_and_validate_sheet_revision(
            sheet,
            serializer.validated_data.get('base_revision'),
        )

        updated = 0
        deleted = 0
        for op in serializer.validated_data['ops']:
            scope = op['scope']
            operation = op['operation']
            row_index = op.get('row')
            col_index = op.get('col')

            # Normalize storage so ROW and COLUMN highlights are consistent with pattern engine:
            # - CELL: row_index=row, col_index=col
            # - ROW:  row_index=row, col_index=0
            # - COLUMN: row_index=0, col_index=col
            if scope == SpreadsheetHighlightScope.CELL:
                norm_row = row_index
                norm_col = col_index
            elif scope == SpreadsheetHighlightScope.ROW:
                norm_row = row_index
                norm_col = 0
            elif scope == SpreadsheetHighlightScope.COLUMN:
                norm_row = 0
                norm_col = col_index
            else:
                # Fallback: keep as-is (should not normally happen)
                norm_row = row_index
                norm_col = col_index

            if operation == 'SET':
                color = op['color']
                SpreadsheetHighlight.objects.update_or_create(
                    sheet=sheet,
                    scope=scope,
                    row_index=norm_row,
                    col_index=norm_col,
                    defaults={
                        'spreadsheet': spreadsheet,
                        'color': color,
                    },
                )
                updated += 1
            else:
                # CLEAR: delete any matching highlight records for this logical target.
                qs = SpreadsheetHighlight.objects.filter(sheet=sheet, scope=scope)
                if scope == SpreadsheetHighlightScope.CELL:
                    qs = qs.filter(row_index=norm_row, col_index=norm_col)
                elif scope == SpreadsheetHighlightScope.ROW:
                    qs = qs.filter(row_index=norm_row)
                elif scope == SpreadsheetHighlightScope.COLUMN:
                    qs = qs.filter(col_index=norm_col)
                deleted += qs.delete()[0]

        return Response({
            'updated': updated,
            'deleted': deleted,
            'revision': sheet.revision,
        })


class SpreadsheetCellFormatListView(APIView):
    """List cell formats for a sheet"""
    permission_classes = [IsAuthenticated]

    def get(self, request, spreadsheet_id, sheet_id):
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)

        formats = SpreadsheetCellFormat.objects.filter(sheet=sheet, is_deleted=False).order_by('row_index', 'column_index')
        serializer = SpreadsheetCellFormatSerializer(formats, many=True)
        return Response({'formats': serializer.data, 'revision': sheet.revision})


class SpreadsheetCellFormatBatchView(APIView):
    """Batch update cell formats"""
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, spreadsheet_id, sheet_id):
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)

        serializer = SpreadsheetCellFormatBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sheet = _lock_and_validate_sheet_revision(
            sheet,
            serializer.validated_data.get('base_revision'),
        )

        updated = 0
        for op in serializer.validated_data['ops']:
            row_index = op['row']
            column_index = op['column']
            defaults = {
                'bold': op.get('bold', False),
                'italic': op.get('italic', False),
                'strikethrough': op.get('strikethrough', False),
                'text_color': op.get('text_color') or None,
                'is_deleted': False,
            }
            if 'font_family' in op:
                defaults['font_family'] = op['font_family'] or None
            if 'font_size' in op:
                defaults['font_size'] = op['font_size']
            if 'number_format' in op:
                defaults['number_format'] = op['number_format']
            SpreadsheetCellFormat.objects.update_or_create(
                sheet=sheet,
                row_index=row_index,
                column_index=column_index,
                defaults=defaults,
            )
            updated += 1

        return Response({'updated': updated, 'revision': sheet.revision})


class SheetXlsxExportView(APIView):
    """Export a sheet to .xlsx, embedding native charts for sparkline cells (MED-295)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, spreadsheet_id, sheet_id):
        spreadsheet = get_accessible_spreadsheet_or_404(
            request.user, **resolve_lookup_kwargs(spreadsheet_id)
        )
        sheet = get_object_or_404(Sheet, id=sheet_id, spreadsheet=spreadsheet, is_deleted=False)
        content = build_sheet_workbook(sheet)
        response = HttpResponse(
            content,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        # sheet.name is user-controlled: strip quotes/backslashes and control
        # chars so it can't break or inject into the Content-Disposition header.
        safe_name = re.sub(r'["\\\r\n]|[\x00-\x1f]', '', (sheet.name or 'sheet')).strip() or 'sheet'
        response['Content-Disposition'] = f'attachment; filename="{safe_name}.xlsx"'
        return response


def _audit_nl_generation(request, sheet, event_type, *, instruction, cols, rows):
    """Record an NL->config generation request. Counts/lengths only, no content."""
    from core.services.audit_events import safe_emit_audit_event

    project = sheet.spreadsheet.project
    safe_emit_audit_event(
        event_type=event_type,
        actor=request.user,
        organization=getattr(project, 'organization', None),
        project=project,
        target_type='spreadsheet',
        target_id=sheet.spreadsheet_id,
        context={
            'sheet_id': sheet.id,
            'cols_sent': cols,
            'rows_sent': rows,
            'instruction_chars': len(instruction),
            'provider': 'gemini',
            'model': 'gemini-2.5-flash-lite',
        },
        request=request,
    )


class GeneratePatternStepsView(APIView):
    """Convert a natural-language instruction into PatternStep[] via Gemini."""
    permission_classes = [IsAuthenticated]

    def post(self, request, sheet_id):
        from .nl_pattern_service import generate_pattern_steps
        from .providers import check_ai_analysis_enabled, AiAnalysisDisabled

        sheet = get_accessible_sheet_or_404(request.user, id=sheet_id)

        try:
            check_ai_analysis_enabled(
                sheet.spreadsheet.project,
                user=request.user,
                spreadsheet=sheet.spreadsheet,
            )
        except AiAnalysisDisabled as exc:
            return Response(
                {'error': exc.message, 'code': exc.code,
                 'spreadsheet_id': exc.spreadsheet_id},
                status=status.HTTP_403_FORBIDDEN,
            )

        instruction = (request.data.get('instruction') or '').strip()
        if not instruction:
            return Response({'error': 'instruction is required.'}, status=status.HTTP_400_BAD_REQUEST)

        # Build sheet schema
        columns = list(
            SheetColumn.objects.filter(sheet=sheet, is_deleted=False).order_by('position')
        )
        # Determine which columns have any non-empty cell
        cols_with_data = set(
            Cell.objects.filter(
                sheet=sheet,
                is_deleted=False,
                column__is_deleted=False,
                row__is_deleted=False,
            )
            .exclude(value_type=CellValueType.EMPTY)
            .values_list('column_id', flat=True)
            .distinct()
        )
        row_count = SheetRow.objects.filter(sheet=sheet, is_deleted=False).count()

        sheet_schema = {
            'columns': [
                {
                    'index': col.index,
                    'name': col.name or f'Column {col.index}',
                    'has_data': col.id in cols_with_data,
                }
                for col in columns
            ],
            'row_count': row_count,
        }

        try:
            steps = generate_pattern_steps(instruction, sheet_schema)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        _audit_nl_generation(
            request, sheet, 'spreadsheet.nl_pattern.generated',
            instruction=instruction, cols=len(sheet_schema['columns']),
            rows=row_count,
        )
        return Response({'steps': steps})


class GeneratePivotConfigView(APIView):
    """Convert a natural-language pivot description into a PivotConfig via Gemini."""
    permission_classes = [IsAuthenticated]

    def post(self, request, sheet_id):
        from .nl_pivot_service import generate_pivot_config
        from .pivot_service import _get_source_columns
        from .providers import check_ai_analysis_enabled, AiAnalysisDisabled

        sheet = get_accessible_sheet_or_404(request.user, id=sheet_id)

        try:
            check_ai_analysis_enabled(
                sheet.spreadsheet.project,
                user=request.user,
                spreadsheet=sheet.spreadsheet,
            )
        except AiAnalysisDisabled as exc:
            return Response(
                {'error': exc.message, 'code': exc.code,
                 'spreadsheet_id': exc.spreadsheet_id},
                status=status.HTTP_403_FORBIDDEN,
            )

        instruction = (request.data.get('instruction') or '').strip()
        if not instruction:
            return Response({'error': 'instruction is required.'}, status=status.HTTP_400_BAD_REQUEST)

        # Use the same header derivation as the pivot engine (row-0 cell content),
        # not SheetColumn.name, so field names Gemini picks match what pivot_engine.py
        # will resolve against when the pivot sheet is recomputed.
        source_columns = _get_source_columns(sheet)

        cols_with_data = set(
            Cell.objects.filter(
                sheet=sheet,
                is_deleted=False,
                column__is_deleted=False,
                row__is_deleted=False,
                row__position__gte=1,
            )
            .exclude(value_type=CellValueType.EMPTY)
            .values_list('column__position', flat=True)
            .distinct()
        )
        row_count = SheetRow.objects.filter(sheet=sheet, is_deleted=False).count()

        sheet_schema = {
            'columns': [
                {
                    'name': col['header'],
                    'has_data': col['index'] in cols_with_data,
                }
                for col in source_columns
            ],
            'row_count': row_count,
        }

        try:
            config = generate_pivot_config(instruction, sheet_schema)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        _audit_nl_generation(
            request, sheet, 'spreadsheet.nl_pivot.generated',
            instruction=instruction, cols=len(sheet_schema['columns']),
            rows=row_count,
        )
        return Response({'config': config})

"""
Campaign Management Module - Views
============================================================================
"""
import logging

from rest_framework import viewsets, status, permissions
from core.slug_mixins import SlugLookupViewSetMixin, resolve_project_pk, resolve_lookup_kwargs
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError as DRFValidationError
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.shortcuts import get_object_or_404
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.contrib.auth import get_user_model
from decimal import Decimal

from core.models import ProjectMember, Project
from core.utils.project import has_project_access
from .services import CampaignService, TemplateService
from .services import CampaignTaskIntegrationService
from .models import (
    Campaign,
    CampaignStatusHistory,
    PerformanceCheckIn,
    PerformanceSnapshot,
    CampaignAttachment,
    CampaignTemplate,
    CampaignTaskLink,
    CampaignDecisionLink,
    CampaignCalendarLink,
)
from .serializers import (
    CampaignSerializer,
    CampaignCreateSerializer,
    CampaignUpdateSerializer,
    CampaignStatusHistorySerializer,
    PerformanceCheckInSerializer,
    PerformanceCheckInCreateSerializer,
    PerformanceSnapshotSerializer,
    PerformanceSnapshotCreateSerializer,
    CampaignAttachmentSerializer,
    CampaignAttachmentCreateSerializer,
    CampaignTemplateSerializer,
    CampaignTaskLinkSerializer,
    CampaignTaskLinkCreateSerializer,
    CampaignDecisionLinkSerializer,
    CampaignDecisionLinkCreateSerializer,
    CampaignCalendarLinkSerializer,
    CampaignCalendarLinkCreateSerializer,
    UserSummarySerializer,
)

logger = logging.getLogger(__name__)

# Campaign fields the budget pacing forecast is computed from.
PACING_INPUT_FIELDS = ('budget_estimate', 'start_date', 'end_date')


# ============================================================================
# Campaign ViewSet
# ============================================================================

class CampaignViewSet(SlugLookupViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for Campaign CRUD operations and status transitions.
    
    Endpoints:
    - GET/POST /api/campaigns/ - List and create campaigns
    - GET/PUT/PATCH/DELETE /api/campaigns/{id}/ - Campaign detail operations
    - POST /api/campaigns/{id}/start-testing/ - Transition to Testing
    - POST /api/campaigns/{id}/start-scaling/ - Transition to Scaling
    - POST /api/campaigns/{id}/start-optimizing/ - Transition to Optimizing
    - POST /api/campaigns/{id}/pause/ - Pause campaign
    - POST /api/campaigns/{id}/resume/ - Resume paused campaign
    - POST /api/campaigns/{id}/complete/ - Mark as completed
    - POST /api/campaigns/{id}/archive/ - Archive campaign
    - POST /api/campaigns/{id}/restore/ - Restore archived campaign
    - GET /api/campaigns/{id}/status-history/ - Get status history
    """
    
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'
    lookup_url_kwarg = 'id'
    
    def get_queryset(self):
        """Filter campaigns by user's project memberships"""
        user = self.request.user
        if not user.is_authenticated:
            return Campaign.objects.none()
        
        # Get all projects where user is a member
        accessible_project_ids = set(
            ProjectMember.objects.filter(
                user=user,
                is_active=True
            ).values_list('project_id', flat=True)
        )
        
        # Check project filter BEFORE early-return so an inaccessible project
        # raises 403 even when the user has no other memberships.
        project_param = self.request.query_params.get('project')
        resolved_pid = None
        if project_param:
            resolved_pid = resolve_project_pk(project_param)
            if resolved_pid is None or resolved_pid not in accessible_project_ids:
                raise PermissionDenied('You do not have access to this project.')

        if not accessible_project_ids:
            return Campaign.objects.none()

        queryset = Campaign.objects.filter(
            project_id__in=accessible_project_ids,
            is_deleted=False
        ).select_related('project', 'owner', 'creator', 'assignee', 'pacing_forecast')

        # Apply filters
        if resolved_pid:
            queryset = queryset.filter(project_id=resolved_pid)
        
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        owner_id = self.request.query_params.get('owner')
        if owner_id:
            queryset = queryset.filter(owner_id=owner_id)
        
        assignee_id = self.request.query_params.get('assignee')
        if assignee_id:
            queryset = queryset.filter(assignee_id=assignee_id)
        
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(hypothesis__icontains=search)
            )
        
        return queryset.order_by('-created_at')
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'create':
            return CampaignCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return CampaignUpdateSerializer
        return CampaignSerializer
    
    def get_serializer_context(self):
        """Add request to serializer context"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def perform_create(self, serializer):
        """Create campaign with automatic creator assignment"""
        campaign = serializer.save()
        # Hook: auto-create budget + asset tasks (URS MVP)
        CampaignTaskIntegrationService.on_campaign_created(campaign=campaign, actor=self.request.user)
    
    def perform_update(self, serializer):
        """Update campaign with validation"""
        instance = serializer.instance
        
        # Check if campaign is archived (cannot be edited except restore)
        if instance.status == Campaign.Status.ARCHIVED:
            raise DRFValidationError({
                'status': 'Archived campaigns cannot be edited. Use restore() to move back to Completed status.'
            })

        pacing_inputs_before = {f: getattr(instance, f) for f in PACING_INPUT_FIELDS}
        campaign = serializer.save()

        if any(getattr(campaign, f) != pacing_inputs_before[f] for f in PACING_INPUT_FIELDS):
            self._refresh_pacing(campaign)

    def _refresh_pacing(self, campaign):
        """Recompute the pacing forecast after its inputs changed.

        Without this, a user who fills in a budget or end date keeps seeing the
        stale forecast until the nightly run. Best-effort: a pacing failure must
        never fail the campaign update itself — the nightly task will catch up.
        """
        from optimization.services import PacingService

        try:
            PacingService.recompute_for_campaign(campaign)
        except Exception:
            logger.exception('Pacing recompute failed for campaign %s', campaign.pk)

    def perform_destroy(self, instance):
        """Soft delete campaign"""
        CampaignService.soft_delete(campaign=instance)
    
    def check_campaign_access(self, campaign):
        """Check if user has access to campaign"""
        CampaignService.assert_campaign_access(user=self.request.user, campaign=campaign)
    
    # Status transition actions

    @action(detail=True, methods=['post'], url_path='save-as-template')
    def save_as_template(self, request, *args, **kwargs):
        """
        Save a campaign as a reusable template.

        Payload (OpenAPI-aligned):
        - name (required)
        - description (optional)
        - sharing_scope (optional; default PERSONAL)
        - project_id (optional; required for TEAM/ORGANIZATION)
        - include_task_checklist/include_decision_triggers (optional; ignored for now)
        """
        campaign = self.get_object()
        template = TemplateService.save_campaign_as_template(
            campaign=campaign,
            user=request.user,
            name=request.data.get('name'),
            description=request.data.get('description'),
            sharing_scope=request.data.get('sharing_scope') or CampaignTemplate.SharingScope.PERSONAL,
            project_id=request.data.get('project_id'),
        )

        return Response(
            CampaignTemplateSerializer(template, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )
    
    @action(detail=True, methods=['post'], url_path='start-testing')
    def start_testing(self, request, *args, **kwargs):
        """Transition campaign from PLANNING to TESTING"""
        campaign = self.get_object()
        CampaignService.transition_status(
            campaign=campaign,
            transition='start_testing',
            user=request.user,
            status_note=request.data.get('status_note', ''),
        )
        
        serializer = self.get_serializer(campaign, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'], url_path='start-scaling')
    def start_scaling(self, request, *args, **kwargs):
        """Transition campaign from TESTING to SCALING"""
        campaign = self.get_object()
        CampaignService.transition_status(
            campaign=campaign,
            transition='start_scaling',
            user=request.user,
            status_note=request.data.get('status_note', ''),
        )
        
        serializer = self.get_serializer(campaign, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'], url_path='start-optimizing')
    def start_optimizing(self, request, *args, **kwargs):
        """Transition campaign to OPTIMIZING"""
        campaign = self.get_object()
        CampaignService.transition_status(
            campaign=campaign,
            transition='start_optimizing',
            user=request.user,
            status_note=request.data.get('status_note', ''),
        )
        
        serializer = self.get_serializer(campaign, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def pause(self, request, *args, **kwargs):
        """Pause campaign"""
        campaign = self.get_object()
        CampaignService.transition_status(
            campaign=campaign,
            transition='pause',
            user=request.user,
            status_note=request.data.get('status_note', ''),
        )
        
        serializer = self.get_serializer(campaign, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def resume(self, request, *args, **kwargs):
        """Resume paused campaign"""
        campaign = self.get_object()
        CampaignService.transition_status(
            campaign=campaign,
            transition='resume',
            user=request.user,
            status_note=request.data.get('status_note', ''),
        )
        
        serializer = self.get_serializer(campaign, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def complete(self, request, *args, **kwargs):
        """Mark campaign as completed"""
        campaign = self.get_object()
        CampaignService.transition_status(
            campaign=campaign,
            transition='complete',
            user=request.user,
            status_note=request.data.get('status_note', ''),
        )
        
        serializer = self.get_serializer(campaign, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def archive(self, request, *args, **kwargs):
        """Archive completed campaign"""
        campaign = self.get_object()
        CampaignService.transition_status(
            campaign=campaign,
            transition='archive',
            user=request.user,
            status_note=request.data.get('status_note', ''),
        )
        
        serializer = self.get_serializer(campaign, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def restore(self, request, *args, **kwargs):
        """Restore archived campaign"""
        campaign = self.get_object()
        CampaignService.transition_status(
            campaign=campaign,
            transition='restore',
            user=request.user,
            status_note=request.data.get('status_note', ''),
        )
        
        serializer = self.get_serializer(campaign, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'], url_path='status-history')
    def status_history(self, request, *args, **kwargs):
        """Get campaign status history"""
        campaign = self.get_object()
        self.check_campaign_access(campaign)
        
        history = campaign.status_history.all().order_by('-created_at')
        serializer = CampaignStatusHistorySerializer(history, many=True, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'], url_path='activity-timeline')
    def activity_timeline(self, request, *args, **kwargs):
        """
        Get unified activity timeline for campaign.
        
        Aggregates status changes, check-ins, and performance snapshots
        into a single chronologically ordered list.
        
        Query Parameters:
        - page: Page number (default: 1, min: 1)
        - page_size: Number of items per page (default: 10, min: 1, max: 100)
        
        Returns:
        {
            'count': int,  # Total number of items
            'results': [...],  # Current page items
            'page': int,  # Current page number
            'page_size': int,  # Items per page
            'next': str | None,  # Next page URL query string
            'previous': str | None  # Previous page URL query string
        }
        """
        # Parse pagination parameters
        try:
            page = int(request.query_params.get('page', 1))
            if page < 1:
                page = 1
        except (ValueError, TypeError):
            return Response(
                {'error': 'Invalid page parameter. Must be a positive integer.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            page_size = int(request.query_params.get('page_size', 10))
            if page_size < 1:
                page_size = 10
            elif page_size > 100:
                page_size = 100
        except (ValueError, TypeError):
            return Response(
                {'error': 'Invalid page_size parameter. Must be an integer between 1 and 100.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get campaign directly to check permissions before queryset filtering
        # Use lookup_url_kwarg to get the campaign ID from URL
        campaign_id = kwargs.get(self.lookup_url_kwarg) or kwargs.get('pk')
        try:
            campaign = Campaign.objects.get(**resolve_lookup_kwargs(campaign_id), is_deleted=False)
        except Campaign.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound('Campaign not found.')
        
        # Check access permission (raises 403 if no access)
        self.check_campaign_access(campaign)
        
        timeline_items = []
        
        # 1. Status history items
        for status_change in campaign.status_history.all().select_related('changed_by'):
            user_data = None
            if status_change.changed_by:
                user_data = UserSummarySerializer(status_change.changed_by, context={'request': request}).data
            timeline_items.append({
                'type': 'status_change',
                'id': str(status_change.id),
                'timestamp': status_change.created_at,
                'user': user_data,
                'details': {
                    'from_status': status_change.from_status,
                    'from_status_display': status_change.get_from_status_display(),
                    'to_status': status_change.to_status,
                    'to_status_display': status_change.get_to_status_display(),
                    'note': status_change.note,
                }
            })
        
        # 2. Check-in items
        for check_in in campaign.check_ins.all().select_related('checked_by'):
            user_data = None
            if check_in.checked_by:
                user_data = UserSummarySerializer(check_in.checked_by, context={'request': request}).data
            timeline_items.append({
                'type': 'check_in',
                'id': str(check_in.id),
                'timestamp': check_in.created_at,
                'user': user_data,
                'details': {
                    'sentiment': check_in.sentiment,
                    'sentiment_display': check_in.get_sentiment_display(),
                    'note': check_in.note,
                }
            })
        
        # 3. Performance snapshot items
        for snapshot in campaign.performance_snapshots.all().select_related('snapshot_by'):
            screenshot_url = None
            if snapshot.screenshot and hasattr(snapshot.screenshot, 'url'):
                screenshot_url = request.build_absolute_uri(snapshot.screenshot.url)
            
            user_data = None
            if snapshot.snapshot_by:
                user_data = UserSummarySerializer(snapshot.snapshot_by, context={'request': request}).data
            
            # Format Decimal values appropriately
            # spend has decimal_places=2, so format with 2 decimal places
            spend_str = f'{snapshot.spend:.2f}' if isinstance(snapshot.spend, Decimal) else str(snapshot.spend)
            
            # metric_value and percentage_change: remove trailing zeros
            def format_decimal_no_trailing_zeros(d):
                """Format Decimal to string, removing trailing zeros"""
                if not isinstance(d, Decimal):
                    return str(d)
                s = str(d)
                if '.' in s:
                    s = s.rstrip('0').rstrip('.')
                return s
            
            metric_value_str = format_decimal_no_trailing_zeros(snapshot.metric_value)
            percentage_change_str = format_decimal_no_trailing_zeros(snapshot.percentage_change) if snapshot.percentage_change else None
            
            timeline_items.append({
                'type': 'performance_snapshot',
                'id': str(snapshot.id),
                'timestamp': snapshot.created_at,
                'user': user_data,
                'details': {
                    'milestone_type': snapshot.milestone_type,
                    'milestone_type_display': snapshot.get_milestone_type_display(),
                    'spend': spend_str,
                    'metric_type': snapshot.metric_type,
                    'metric_type_display': snapshot.get_metric_type_display(),
                    'metric_value': metric_value_str,
                    'percentage_change': percentage_change_str,
                    'notes': snapshot.notes,
                    'screenshot_url': screenshot_url,
                    'additional_metrics': snapshot.additional_metrics,
                }
            })
        
        # Sort by timestamp (newest first)
        timeline_items.sort(key=lambda x: x['timestamp'], reverse=True)
        
        # Pagination
        total_count = len(timeline_items)
        start = (page - 1) * page_size
        end = start + page_size
        paginated_items = timeline_items[start:end]
        
        # Build pagination response
        response_data = {
            'count': total_count,
            'results': paginated_items,
            'page': page,
            'page_size': page_size,
            'next': None,
            'previous': None,
        }
        
        # Add next page query string if there are more items
        if end < total_count:
            response_data['next'] = f'?page={page + 1}&page_size={page_size}'
        
        # Add previous page query string if not on first page
        if page > 1:
            response_data['previous'] = f'?page={page - 1}&page_size={page_size}'
        
        return Response(response_data)


# ============================================================================
# Performance Check-In ViewSet
# ============================================================================

class PerformanceCheckInViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Performance Check-In CRUD operations.
    
    Nested under campaigns: /api/campaigns/{campaign_id}/check-ins/
    """
    
    permission_classes = [IsAuthenticated]
    serializer_class = PerformanceCheckInSerializer
    lookup_field = 'id'
    lookup_url_kwarg = 'id'
    
    def get_queryset(self):
        """Filter check-ins by campaign"""
        campaign_id = self.kwargs.get('campaign_id')
        if not campaign_id:
            return PerformanceCheckIn.objects.none()
        
        # Verify campaign access
        try:
            campaign = Campaign.objects.get(**resolve_lookup_kwargs(campaign_id), is_deleted=False)
        except Campaign.DoesNotExist:
            return PerformanceCheckIn.objects.none()
        
        if not has_project_access(self.request.user, campaign.project):
            raise PermissionDenied('You do not have access to this campaign.')
        
        queryset = PerformanceCheckIn.objects.filter(
            campaign=campaign
        ).select_related('campaign', 'checked_by')
        
        # Apply filters
        sentiment = self.request.query_params.get('sentiment')
        if sentiment:
            queryset = queryset.filter(sentiment=sentiment)
        
        return queryset.order_by('-created_at')
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'create':
            return PerformanceCheckInCreateSerializer
        return PerformanceCheckInSerializer
    
    def get_serializer_context(self):
        """Add request to serializer context"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def perform_create(self, serializer):
        """Create check-in with automatic user assignment"""
        campaign_id = self.kwargs.get('campaign_id')
        try:
            campaign = Campaign.objects.get(**resolve_lookup_kwargs(campaign_id), is_deleted=False)
        except Campaign.DoesNotExist:
            raise DRFValidationError({'campaign': 'Campaign not found'})
        
        if not has_project_access(self.request.user, campaign.project):
            raise PermissionDenied('You do not have access to this campaign.')
        
        serializer.save(campaign=campaign)


# ============================================================================
# Performance Snapshot ViewSet
# ============================================================================

class PerformanceSnapshotViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Performance Snapshot CRUD operations.
    
    Nested under campaigns: /api/campaigns/{campaign_id}/performance-snapshots/
    """
    
    permission_classes = [IsAuthenticated]
    serializer_class = PerformanceSnapshotSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    lookup_field = 'id'
    lookup_url_kwarg = 'id'
    
    def get_queryset(self):
        """Filter snapshots by campaign"""
        campaign_id = self.kwargs.get('campaign_id')
        if not campaign_id:
            return PerformanceSnapshot.objects.none()
        
        # Verify campaign access
        try:
            campaign = Campaign.objects.get(**resolve_lookup_kwargs(campaign_id), is_deleted=False)
        except Campaign.DoesNotExist:
            return PerformanceSnapshot.objects.none()
        
        if not has_project_access(self.request.user, campaign.project):
            raise PermissionDenied('You do not have access to this campaign.')
        
        queryset = PerformanceSnapshot.objects.filter(
            campaign=campaign
        ).select_related('campaign', 'snapshot_by')
        
        # Apply filters
        milestone_type = self.request.query_params.get('milestone_type')
        if milestone_type:
            queryset = queryset.filter(milestone_type=milestone_type)
        
        metric_type = self.request.query_params.get('metric_type')
        if metric_type:
            queryset = queryset.filter(metric_type=metric_type)
        
        return queryset.order_by('-created_at')
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'create':
            return PerformanceSnapshotCreateSerializer
        return PerformanceSnapshotSerializer
    
    def get_serializer_context(self):
        """Add request to serializer context"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def perform_create(self, serializer):
        """Create snapshot with automatic user assignment"""
        campaign_id = self.kwargs.get('campaign_id')
        try:
            campaign = Campaign.objects.get(**resolve_lookup_kwargs(campaign_id), is_deleted=False)
        except Campaign.DoesNotExist:
            raise DRFValidationError({'campaign': 'Campaign not found'})
        
        if not has_project_access(self.request.user, campaign.project):
            raise PermissionDenied('You do not have access to this campaign.')
        
        serializer.save(campaign=campaign)
    
    @action(detail=True, methods=['post'], url_path='screenshot', parser_classes=[MultiPartParser, FormParser])
    def upload_screenshot(self, request, campaign_id=None, id=None):
        """Upload screenshot for a performance snapshot"""
        snapshot = self.get_object()
        
        # Verify campaign access
        if not has_project_access(self.request.user, snapshot.campaign.project):
            raise PermissionDenied('You do not have access to this campaign.')
        
        if 'file' not in request.FILES:
            raise DRFValidationError({'file': 'File is required'})
        
        snapshot.screenshot = request.FILES['file']
        snapshot.save(update_fields=['screenshot'])
        
        serializer = self.get_serializer(snapshot, context={'request': request})
        return Response(serializer.data)


# ============================================================================
# Campaign Attachment ViewSet
# ============================================================================

class CampaignAttachmentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Campaign Attachment CRUD operations.
    
    Nested under campaigns: /api/campaigns/{id}/attachments/
    """
    
    permission_classes = [IsAuthenticated]
    serializer_class = CampaignAttachmentSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    lookup_field = 'id'
    lookup_url_kwarg = 'attachment_id'
    
    def get_queryset(self):
        """Filter attachments by campaign"""
        campaign_id = self.kwargs.get('campaign_id') or self.kwargs.get('id')
        if not campaign_id:
            return CampaignAttachment.objects.none()
        
        # Verify campaign access
        try:
            campaign = Campaign.objects.get(**resolve_lookup_kwargs(campaign_id), is_deleted=False)
        except Campaign.DoesNotExist:
            return CampaignAttachment.objects.none()
        
        if not has_project_access(self.request.user, campaign.project):
            raise PermissionDenied('You do not have access to this campaign.')
        
        queryset = CampaignAttachment.objects.filter(
            campaign=campaign
        ).select_related('campaign', 'uploaded_by')
        
        # Apply filters
        asset_type = self.request.query_params.get('asset_type')
        if asset_type:
            queryset = queryset.filter(asset_type=asset_type)
        
        return queryset.order_by('-created_at')
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'create':
            return CampaignAttachmentCreateSerializer
        return CampaignAttachmentSerializer
    
    def get_serializer_context(self):
        """Add request to serializer context"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def perform_create(self, serializer):
        """Create attachment with automatic user assignment"""
        campaign_id = self.kwargs.get('campaign_id') or self.kwargs.get('id')
        try:
            campaign = Campaign.objects.get(**resolve_lookup_kwargs(campaign_id), is_deleted=False)
        except Campaign.DoesNotExist:
            raise DRFValidationError({'campaign': 'Campaign not found'})
        
        if not has_project_access(self.request.user, campaign.project):
            raise PermissionDenied('You do not have access to this campaign.')
        
        serializer.save(campaign=campaign)
    
    def create(self, request, *args, **kwargs):
        """Override create to return full serializer"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        # Return full serializer instead of create serializer
        full_serializer = CampaignAttachmentSerializer(serializer.instance, context={'request': request})
        return Response(full_serializer.data, status=status.HTTP_201_CREATED, headers=headers)


# ============================================================================
# Campaign Template ViewSet
# ============================================================================

class CampaignTemplateViewSet(SlugLookupViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for Campaign Template CRUD operations.
    
    Endpoints:
    - GET/POST /api/campaign-templates/ - List and create templates
    - GET/PUT/PATCH/DELETE /api/campaign-templates/{id}/ - Template detail operations
    """
    
    permission_classes = [IsAuthenticated]
    serializer_class = CampaignTemplateSerializer
    lookup_field = 'id'
    lookup_url_kwarg = 'id'

    def get_object(self):
        """
        Allow alternate URL kwarg names for OpenAPI alignment.

        - Spec uses {template_id} for create-campaign.
        - Router uses {id} for standard detail routes.
        """
        if 'template_id' in self.kwargs and self.lookup_url_kwarg not in self.kwargs:
            self.kwargs[self.lookup_url_kwarg] = self.kwargs['template_id']
        return super().get_object()
    
    def get_serializer_context(self):
        """Add request to serializer context"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def get_queryset(self):
        """Filter templates by sharing scope and user access"""
        user = self.request.user
        queryset = CampaignTemplate.objects.filter(is_deleted=False, is_archived=False)
        
        # Apply filters
        sharing_scope = self.request.query_params.get('sharing_scope')
        if sharing_scope:
            queryset = queryset.filter(sharing_scope=sharing_scope)
        
        project_id = self.request.query_params.get('project')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        
        # Filter by creator for personal templates
        creator_id = self.request.query_params.get('creator')
        if creator_id:
            queryset = queryset.filter(creator_id=creator_id)
        
        # Access control: show personal templates, team/org templates user has access to
        accessible_project_ids = set(
            ProjectMember.objects.filter(
                user=user,
                is_active=True
            ).values_list('project_id', flat=True)
        )
        
        queryset = queryset.filter(
            Q(creator=user, sharing_scope='PERSONAL') |
            Q(sharing_scope='TEAM', project_id__in=accessible_project_ids) |
            Q(sharing_scope='ORGANIZATION', project_id__in=accessible_project_ids)
        )
        
        return queryset.select_related('creator', 'project').order_by('-created_at')
    
    def perform_create(self, serializer):
        """Create template with automatic creator assignment"""
        serializer.save()

    @action(detail=True, methods=['post'], url_path='create-campaign')
    def create_campaign(self, request, *args, **kwargs):
        """
        Create a new campaign from this template.

        Request body (OpenAPI-aligned, plus start_date requirement from Campaign model):
        - name (required)
        - project (required)  (uuid)
        - owner (required)    (uuid)
        - start_date (optional; defaults to today if omitted)
        - end_date (optional)
        """
        template = self.get_object()

        campaign = TemplateService.create_campaign_from_template(
            template=template,
            user=request.user,
            name=request.data.get('name'),
            project_id=request.data.get('project'),
            owner_id=request.data.get('owner'),
            start_date=request.data.get('start_date'),
            end_date=request.data.get('end_date'),
            assignee_id=request.data.get('assignee'),
            objective=request.data.get('objective'),
            platforms=request.data.get('platforms'),
            hypothesis=request.data.get('hypothesis'),
            tags=request.data.get('tags'),
            budget_estimate=request.data.get('budget_estimate'),
        )

        return Response(CampaignSerializer(campaign, context={'request': request}).data, status=status.HTTP_201_CREATED)


# ============================================================================
# Integration: Campaign ↔ Task Links (OpenAPI: /campaign-task-links/)
# ============================================================================

class CampaignTaskLinkViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'
    lookup_url_kwarg = 'id'

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return CampaignTaskLink.objects.none()

        accessible_project_ids = set(
            ProjectMember.objects.filter(user=user, is_active=True).values_list('project_id', flat=True)
        )
        return CampaignTaskLink.objects.select_related(
            'campaign', 'campaign__project', 'task', 'task__project'
        ).filter(campaign__project_id__in=accessible_project_ids)

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        campaign_id = request.query_params.get('campaign')
        task_id = request.query_params.get('task')
        if campaign_id:
            campaign = Campaign.objects.filter(**resolve_lookup_kwargs(campaign_id)).first()
            qs = qs.filter(campaign=campaign) if campaign else qs.none()
        if task_id:
            qs = qs.filter(task_id=task_id)
        serializer = CampaignTaskLinkSerializer(qs.order_by('-created_at'), many=True, context={'request': request})
        return Response({'results': serializer.data})

    def create(self, request, *args, **kwargs):
        serializer = CampaignTaskLinkCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        link, created = serializer.save()
        response_data = CampaignTaskLinkSerializer(link, context={'request': request}).data
        status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(response_data, status=status_code)

    def destroy(self, request, *args, **kwargs):
        link = get_object_or_404(self.get_queryset(), id=kwargs.get('id'))
        link.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ============================================================================
# Integration: Campaign ↔ Decision Links (OpenAPI: /campaign-decision-links/)
# ============================================================================

class CampaignDecisionLinkViewSet(viewsets.GenericViewSet):
    """
    ViewSet for Campaign-Decision Link CRUD operations.
    
    Endpoints:
    - GET/POST /api/campaign-decision-links/ - List and create links
    - DELETE /api/campaign-decision-links/{id}/ - Delete link
    """
    
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter links by campaign/decision"""
        queryset = CampaignDecisionLink.objects.select_related('campaign', 'decision')
        
        campaign_id = self.request.query_params.get('campaign')
        if campaign_id:
            try:
                campaign = Campaign.objects.get(**resolve_lookup_kwargs(campaign_id), is_deleted=False)
                if not has_project_access(self.request.user, campaign.project):
                    raise PermissionDenied('You do not have access to this campaign')
                queryset = queryset.filter(campaign_id=campaign_id)
            except Campaign.DoesNotExist:
                return CampaignDecisionLink.objects.none()
        
        decision_id = self.request.query_params.get('decision')
        if decision_id:
            queryset = queryset.filter(decision_id=decision_id)
        
        return queryset.order_by('-created_at')
    
    def get_serializer_class(self):
        if self.action == 'create':
            return CampaignDecisionLinkCreateSerializer
        return CampaignDecisionLinkSerializer
    
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link = serializer.save()
        return Response(
            CampaignDecisionLinkSerializer(link, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )


# ============================================================================
# Integration: Campaign ↔ Calendar Links (OpenAPI: /campaign-calendar-links/)
# ============================================================================

class CampaignCalendarLinkViewSet(viewsets.GenericViewSet):
    """
    ViewSet for Campaign-Calendar Link CRUD operations.
    
    Endpoints:
    - GET/POST /api/campaign-calendar-links/ - List and create links
    - DELETE /api/campaign-calendar-links/{id}/ - Delete link
    """
    
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter links by campaign/event"""
        queryset = CampaignCalendarLink.objects.select_related('campaign', 'event')
        
        campaign_id = self.request.query_params.get('campaign')
        if campaign_id:
            try:
                campaign = Campaign.objects.get(**resolve_lookup_kwargs(campaign_id), is_deleted=False)
                if not has_project_access(self.request.user, campaign.project):
                    raise PermissionDenied('You do not have access to this campaign')
                queryset = queryset.filter(campaign_id=campaign_id)
            except Campaign.DoesNotExist:
                return CampaignCalendarLink.objects.none()
        
        event_id = self.request.query_params.get('event')
        if event_id:
            queryset = queryset.filter(event_id=event_id)
        
        return queryset.order_by('-created_at')
    
    def get_serializer_class(self):
        if self.action == 'create':
            return CampaignCalendarLinkCreateSerializer
        return CampaignCalendarLinkSerializer
    
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link = serializer.save()
        return Response(
            CampaignCalendarLinkSerializer(link, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )

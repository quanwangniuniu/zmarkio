from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Q, Case, When, IntegerField, Value
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from core.admin_permissions import IsCsmAccessAllowed
from core.models import Team
from core.permissions import IsProjectMember
from core.viewset_mixins import ProjectScopedViewSetMixin
from core.slug_mixins import SlugLookupViewSetMixin

from .models import (
    Queue, QueueAgent, QueueTeam, CustomerUser, Ticket, CsmNotification,
    Conversation, ConversationMessage, QuickReplyTemplate, QuickReplyTemplateHistory,
    TemplateTag,
    TicketForm, TicketFormAssignment, SupportProject, CsmWorkType,
    SupportChannel, SLAPolicy, SLAPriorityTarget, BusinessHoursCalendar,
    RoutingRule,
)
from .serializers import (
    QueueSerializer, QueueAgentSerializer,
    QueueTeamSerializer, CustomerUserSerializer,
    CsmNotificationSerializer,
    ConversationSerializer, ConversationDetailSerializer,
    ConversationMessageSerializer, TicketSerializer,
    QuickReplyTemplateSerializer, QuickReplyTemplateHistorySerializer,
    TemplateTagSerializer,
    TicketFormListSerializer,
    TicketFormDetailSerializer,
    TicketFormCreateSerializer,
    BulkFieldsSerializer,
    TicketFormAssignmentSerializer,
    ReplaceAssignmentsSerializer,
    SupportProjectSerializer,
    CsmWorkTypeSerializer,
    WorkTypeReorderSerializer,
    SLAPolicySerializer,
    BusinessHoursCalendarSerializer,
    SupportChannelListSerializer,
    SupportChannelDetailSerializer,
    SupportChannelCreateUpdateSerializer,
    ReplaceChannelAssignmentsSerializer,
    TicketStatusSerializer,
    TicketStatusCreateSerializer,
    TicketAutoResolveConfigSerializer,
    StatusMachineSerializer,
    ReplaceTransitionsSerializer,
    RoutingRuleSerializer,
    RoutingRuleWriteSerializer,
    RoutingRuleReorderSerializer,
    RoutingSandboxRequestSerializer,
)
from .models import (
    TicketStatus, TicketStatusTransition, TicketAutoResolveConfig,
)
from .services import (
    ensure_system_fields,
    set_default_form,
    bulk_update_fields,
    replace_assignments,
    assert_can_delete_form,
)
from .services.support_projects import (
    list_support_projects_for_workspace,
    create_support_project,
    update_support_project,
    archive_support_project,
)
from .services.work_types import (
    list_work_types_for_workspace,
    create_work_type,
    update_work_type,
    deactivate_work_type,
    reorder_work_types,
)
from .services.support_channels import (
    list_channels_for_project,
    channel_detail_queryset,
    create_support_channel,
    update_support_channel,
    deactivate_support_channel,
    replace_experience_group_assignments,
    build_embed_snippet,
)
from .services.routing_rules import (
    vocabulary_payload,
    list_rules,
    create_rule,
    update_rule,
    delete_rule,
    reorder_rules,
)
from .services.routing_sandbox import run_sandbox


def _raise_drf_validation(exc):
    raise ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)


SUPPORTED_MESSAGE_IMAGE_TYPES = {
    'image/png', 'image/jpeg', 'image/gif', 'image/webp',
    'image/heic', 'image/heif', 'image/heic-sequence', 'image/heif-sequence',
}
SUPPORTED_MESSAGE_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'heic', 'heif'}
MAX_MESSAGE_IMAGE_BYTES = 10 * 1024 * 1024


def _is_supported_message_image(image):
    content_type = (getattr(image, 'content_type', '') or '').lower()
    extension = image.name.rsplit('.', 1)[-1].lower() if '.' in image.name else ''
    return content_type in SUPPORTED_MESSAGE_IMAGE_TYPES or extension in SUPPORTED_MESSAGE_IMAGE_EXTENSIONS


class QueueViewSet(SlugLookupViewSetMixin, viewsets.ModelViewSet):
    # Slug-only lookups; numeric path segments return 404.
    """
    Queue CRUD.

    - GET    /projects/{pid}/queues/     list
    - POST   /projects/{pid}/queues/     create
    - GET    /queues/{id}/               retrieve
    - PATCH  /queues/{id}/               update
    - DELETE /queues/{id}/               soft delete
    """
    serializer_class = QueueSerializer
    permission_classes = [IsAuthenticated, IsCsmAccessAllowed]

    def get_queryset(self):
        from core.admin_utils import get_csm_admin_org_ids

        queryset = Queue.objects.filter(is_active=True).select_related('organisation')
        user = self.request.user

        org_id = self.request.query_params.get('organisation')
        if org_id:
            queryset = queryset.filter(organisation_id=org_id)

        admin_org_ids = get_csm_admin_org_ids(user)
        return queryset.filter(organisation_id__in=admin_org_ids)

    def perform_destroy(self, instance):
        """Soft delete: mark as inactive instead of removing from DB."""
        instance.is_active = False
        instance.save()

    @action(detail=True, methods=['get'])
    def tickets(self, request, pk=None):
        """GET /queues/{id}/tickets/ — list all tickets in this queue."""
        queue = self.get_object()
        qs = Ticket.objects.filter(queue=queue).select_related('assigned_to').order_by('-created_at')

        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        return Response(TicketSerializer(qs, many=True).data)

    @action(detail=True, methods=['get'])
    def ticket_counts(self, request, pk=None):
        """GET /queues/{id}/ticket_counts/"""
        queue = self.get_object()
        counts = Ticket.objects.filter(queue=queue).aggregate(
            todo=Count('id', filter=Q(status='todo')),
            in_progress=Count('id', filter=Q(status='in_progress')),
        )
        return Response(counts)


class QueueAgentViewSet(viewsets.ModelViewSet):
    """Manage agent assignments within a queue."""
    serializer_class = QueueAgentSerializer
    permission_classes = [IsAuthenticated, IsCsmAccessAllowed]
    http_method_names = ['get', 'post', 'delete']

    def get_queryset(self):
        queue_id = self.kwargs.get('queue_id')
        return QueueAgent.objects.filter(queue_id=queue_id)

    def perform_create(self, serializer):
        queue_id = self.kwargs.get('queue_id')
        serializer.save(
            queue_id=queue_id,
            assigned_by=self.request.user,
        )


class QueueTeamViewSet(viewsets.ModelViewSet):
    """Manage team assignments within a queue."""
    serializer_class = QueueTeamSerializer
    permission_classes = [IsAuthenticated, IsCsmAccessAllowed]
    http_method_names = ['get', 'post', 'delete']

    def get_queryset(self):
        queue_id = self.kwargs.get('queue_id')
        return QueueTeam.objects.filter(queue_id=queue_id)

    def perform_create(self, serializer):
        queue_id = self.kwargs.get('queue_id')
        serializer.save(queue_id=queue_id)


class CustomerUserViewSet(viewsets.ModelViewSet):
    """
    CSM user management.

    - GET    /projects/{pid}/customer-users/     list
    - POST   /projects/{pid}/customer-users/     create
    - PATCH  /customer-users/{id}/               update
    - DELETE /customer-users/{id}/               delete
    """
    serializer_class = CustomerUserSerializer
    permission_classes = [IsAuthenticated, IsCsmAccessAllowed]

    def get_queryset(self):
        from core.admin_utils import get_csm_admin_org_ids

        qs = CustomerUser.objects.select_related('user', 'team', 'queue', 'organisation')
        user = self.request.user

        org_id = self.request.query_params.get('organisation')
        if org_id:
            qs = qs.filter(organisation_id=org_id)

        admin_org_ids = get_csm_admin_org_ids(user)
        return qs.filter(organisation_id__in=admin_org_ids)

    def perform_create(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        if instance.is_creator:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': 'Cannot delete the organisation creator.'})
        instance.delete()

    @action(detail=False, methods=['post'])
    def invite(self, request):
        """Invite an existing user to a CSM organisation via in-app notification."""
        from core.admin_utils import get_csm_admin_org_ids
        from .models import CsmNotification
        from django.contrib.auth import get_user_model
        User = get_user_model()

        org_id = request.data.get('organisation')
        user_id = request.data.get('user_id')
        user_type = request.data.get('user_type', 'agent')
        message = request.data.get('message', '')

        if not org_id or not user_id:
            return Response(
                {'detail': 'organisation and user_id are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        admin_org_ids = get_csm_admin_org_ids(request.user)
        try:
            org_id = int(org_id)
        except (TypeError, ValueError):
            return Response(
                {'detail': 'Invalid organisation ID.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if org_id not in admin_org_ids:
            return Response(
                {'detail': 'You are not an admin of this organisation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            target_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response(
                {'detail': 'User not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if CustomerUser.objects.filter(user=target_user, organisation_id=org_id).exists():
            return Response(
                {'detail': 'User is already a member of this organisation.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check for existing pending invitation
        if CsmNotification.objects.filter(
            recipient=target_user,
            organisation_id=org_id,
            notification_type='org_invitation',
            action_status='pending',
        ).exists():
            return Response(
                {'detail': 'A pending invitation already exists for this user.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from customer.models import CustomerOrganisation
        try:
            org = CustomerOrganisation.objects.get(id=org_id)
        except CustomerOrganisation.DoesNotExist:
            return Response(
                {'detail': 'Organisation not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        CsmNotification.objects.create(
            recipient=target_user,
            sender=request.user,
            notification_type='org_invitation',
            title=f'Invitation to join {org.name}',
            message=message,
            metadata={'user_type': user_type, 'organisation_id': org_id},
            organisation=org,
        )

        return Response(
            {'detail': f'Invitation sent to {target_user.email}.'},
            status=status.HTTP_201_CREATED,
        )



def _send_ticket_confirmation(conversation):
    """
    AC5: notify the customer with the channel's configured confirmation message
    after a ticket is created from their conversation. No-op when the conversation's
    support channel has no confirmation message configured.
    """
    channel = conversation.support_channel
    message_text = (channel.ticket_confirmation_message or '').strip() if channel else ''
    if not message_text:
        return None

    msg = ConversationMessage.objects.create(
        conversation=conversation,
        sender_type='system',
        content=message_text,
    )

    channel_layer = get_channel_layer()
    # Agent side: appears in the workspace thread.
    async_to_sync(channel_layer.group_send)(
        f'csm_conversation_{conversation.id}',
        {'type': 'conversation.message', 'message': ConversationMessageSerializer(msg).data},
    )
    # Customer side: delivered to the customer portal in real time.
    from portal.serializers import PortalMessageSerializer
    async_to_sync(channel_layer.group_send)(
        f'portal_conversation_{conversation.id}',
        {'type': 'conversation.message', 'message': PortalMessageSerializer(msg).data},
    )
    return msg


class ConversationPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


class ConversationViewSet(viewsets.ModelViewSet):
    """
    Agent conversation workspace endpoints.

    - GET    /conversations/                     list (filtered to agent's queues)
    - POST   /conversations/                     create
    - GET    /conversations/{id}/                retrieve (with messages + customer profile)
    - PATCH  /conversations/{id}/                update status/queue/assigned_to/tags
    - POST   /conversations/{id}/messages/       send a message
    - POST   /conversations/{id}/create_ticket/  create ticket from conversation
    """
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    pagination_class = ConversationPagination

    def _accessible_queues(self):
        user = self.request.user
        qs = Queue.objects.filter(is_active=True)
        if user.is_staff or user.is_superuser:
            return qs

        admin_org_ids = CustomerUser.objects.filter(
            user=user,
            is_active=True,
            user_type__in=('supervisor', 'admin'),
            organisation__isnull=False,
        ).values_list('organisation_id', flat=True)
        agent_queue_ids = QueueAgent.objects.filter(user=user).values_list('queue_id', flat=True)
        profile_queue_ids = CustomerUser.objects.filter(
            user=user,
            is_active=True,
            queue__isnull=False,
        ).values_list('queue_id', flat=True)

        return qs.filter(
            Q(organisation_id__in=admin_org_ids)
            | Q(id__in=agent_queue_ids)
            | Q(id__in=profile_queue_ids)
        ).distinct()

    def get_queryset(self):
        user = self.request.user
        base_qs = Conversation.objects.select_related('customer', 'queue', 'assigned_to__user').prefetch_related('tickets')

        # Permission-based filtering
        if user.is_staff or user.is_superuser:
            qs = base_qs.all()
        else:
            accessible_queue_ids = self._accessible_queues().values_list('id', flat=True)
            qs = base_qs.filter(queue_id__in=accessible_queue_ids)

        # Optional queue filter from query params
        queue_id = self.request.query_params.get('queue')
        if queue_id:
            qs = qs.filter(queue_id=queue_id)

        # Status filter — defaults to active+pending for the agent inbox
        status = self.request.query_params.get('status')
        if status:
            qs = qs.filter(status=status)
        elif self.action == 'list':
            qs = qs.filter(status__in=['active', 'pending'])

        return qs

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ConversationDetailSerializer
        return ConversationSerializer

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        # Prefetch messages for detail view
        instance._prefetched_objects_cache = {}
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def available_queues(self, request):
        """
        GET /conversations/available_queues/
        Queues the current agent can see/use in the conversation workspace.
        """
        qs = self._accessible_queues().select_related('organisation')
        org_id = request.query_params.get('organisation')
        if org_id:
            qs = qs.filter(organisation_id=org_id)
        return Response(QueueSerializer(qs, many=True).data)

    @action(detail=True, methods=['get'])
    def assignable_agents(self, request, pk=None):
        """
        GET /conversations/{id}/assignable_agents/
        List active CSM users in the conversation's organisation that this
        conversation can be reassigned to. Each entry's ``id`` is the CustomerUser
        id used for ``assigned_to``. Visible to any agent who can see the
        conversation (get_object enforces queue-based visibility).
        """
        conversation = self.get_object()
        if not conversation.queue_id:
            return Response([])

        queue_agent_user_ids = set(
            QueueAgent.objects.filter(queue=conversation.queue)
            .values_list('user_id', flat=True)
        )
        qs = CustomerUser.objects.filter(
            organisation_id=conversation.queue.organisation_id,
            is_active=True,
            user_type__in=('agent', 'supervisor', 'admin'),
        ).select_related('user')

        # A user may have multiple CustomerUser rows (one per queue). Keep one per
        # person, preferring the profile scoped to this conversation's queue.
        best = {}
        for cu in qs:
            if (
                cu.user_type == 'agent'
                and cu.queue_id != conversation.queue_id
                and cu.user_id not in queue_agent_user_ids
            ):
                continue
            current = best.get(cu.user_id)
            if current is None or (
                cu.queue_id == conversation.queue_id
                and current.queue_id != conversation.queue_id
            ):
                best[cu.user_id] = cu

        agents = []
        for cu in best.values():
            full = cu.user.get_full_name()
            agents.append({
                'id': cu.id,
                'name': full if full.strip() else cu.user.email,
                'email': cu.user.email,
            })
        agents.sort(key=lambda a: a['name'].lower())
        return Response(agents)

    @action(detail=True, methods=['post'])
    def claim(self, request, pk=None):
        """
        POST /conversations/{id}/claim/
        Agent claims a conversation: auto-creates a Ticket linked to it,
        assigns it to the current user. If already claimed by this user, returns existing ticket.
        """
        conversation = self.get_object()

        if not conversation.queue_id:
            # Auto-assign to a queue the current user can actually access (in priority order):
            # 1. A queue this agent is explicitly assigned to
            # 2. A queue in an org where this user is admin/supervisor
            # 3. Any queue (last resort)
            queue = None
            agent_queue_qs = QueueAgent.objects.filter(user=request.user).select_related('queue')
            if agent_queue_qs.exists():
                queue = agent_queue_qs.first().queue
            if not queue:
                admin_org_ids = CustomerUser.objects.filter(
                    user=request.user, is_active=True, user_type__in=('supervisor', 'admin')
                ).values_list('organisation_id', flat=True)
                if admin_org_ids:
                    queue = Queue.objects.filter(organisation_id__in=admin_org_ids).first()
            if not queue:
                return Response(
                    {'detail': 'No queue available. Please contact your administrator.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            Conversation.objects.filter(id=conversation.id).update(queue=queue)
            conversation.refresh_from_db()

        # 1:1 enforcement: one conversation = one ticket (any agent's)
        existing = conversation.tickets.first()
        if existing:
            return Response(TicketSerializer(existing).data, status=status.HTTP_200_OK)

        # Auto-fill title from subject (stored in tags[0]) or fallback
        title = conversation.tags[0] if conversation.tags else f'Conversation #{conversation.id}'
        first_msg = conversation.messages.filter(sender_type='customer').first()
        description = first_msg.content if first_msg else ''

        ticket = Ticket.objects.create(
            queue_id=conversation.queue_id,
            title=title,
            description=description,
            priority='medium',
            status='in_progress',
            assigned_to=request.user,
            customer_email=conversation.customer.email if conversation.customer else '',
            conversation=conversation,
        )
        from csm.services.sla import recalculate_ticket_sla
        recalculate_ticket_sla(ticket)
        if ticket.first_response_due is not None or ticket.resolution_due is not None:
            ticket.save(update_fields=['first_response_due', 'resolution_due'])

        Conversation.objects.filter(id=conversation.id).update(status='active')
        conversation.refresh_from_db()

        system_msg = ConversationMessage.objects.create(
            conversation=conversation,
            sender_type='system',
            content=f'Ticket #{ticket.id} claimed by {request.user.get_full_name() or request.user.email}.',
        )
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'csm_conversation_{conversation.id}',
            {'type': 'conversation.message', 'message': ConversationMessageSerializer(system_msg).data},
        )
        # Broadcast updated conversation to all agents (refreshes list)
        async_to_sync(channel_layer.group_send)(
            'csm_new_conversations',
            {'type': 'conversation.updated', 'conversation': ConversationSerializer(conversation).data},
        )

        # AC5: deliver the channel's configured confirmation message to the customer.
        _send_ticket_confirmation(conversation)

        return Response(TicketSerializer(ticket).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def messages(self, request, pk=None):
        """POST /conversations/{id}/messages/ — send a message as the current agent."""
        conversation = self.get_object()
        customer_user = CustomerUser.objects.filter(user=request.user, is_active=True).first()

        image = request.FILES.get('image')
        if image:
            if not _is_supported_message_image(image):
                return Response(
                    {'detail': 'Only PNG, JPG, GIF, WebP, or HEIC images can be attached.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if image.size > MAX_MESSAGE_IMAGE_BYTES:
                return Response({'detail': 'Image must be under 10MB.'}, status=status.HTTP_400_BAD_REQUEST)
        msg = ConversationMessage.objects.create(
            conversation=conversation,
            sender_type='agent',
            sender_agent=customer_user,
            content=request.data.get('content', ''),
            rich_body=request.data.get('rich_body') if not image else None,
            image=image,
        )

        # The agent's first reply meets the first-response SLA, stopping that leg
        # of the countdown for any ticket on this conversation.
        conversation.tickets.filter(first_responded_at__isnull=True).update(
            first_responded_at=timezone.now(),
        )

        payload = ConversationMessageSerializer(msg, context={'request': request}).data

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'csm_conversation_{conversation.id}',
            {'type': 'conversation.message', 'message': payload},
        )
        # Broadcast to customer portal so they receive the agent reply in real-time
        from portal.serializers import PortalMessageSerializer
        async_to_sync(channel_layer.group_send)(
            f'portal_conversation_{conversation.id}',
            {'type': 'conversation.message', 'message': PortalMessageSerializer(msg, context={'request': request}).data},
        )
        # Broadcast conversation_updated so all agents' lists refresh
        conversation.refresh_from_db()
        async_to_sync(channel_layer.group_send)(
            'csm_new_conversations',
            {'type': 'conversation.updated', 'conversation': ConversationSerializer(conversation).data},
        )

        return Response(payload, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def create_ticket(self, request, pk=None):
        """
        POST /conversations/{id}/create_ticket/
        Creates a Ticket pre-populated from the linked customer profile,
        establishes a bidirectional link, and posts a system message.
        """
        conversation = self.get_object()
        customer = conversation.customer

        # Enforce 1 conversation : 1 ticket (mirrors the claim flow).
        existing = conversation.tickets.first()
        if existing:
            return Response(
                {'detail': 'This conversation already has a linked ticket.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        title = request.data.get('title', f'Ticket from conversation #{conversation.id}')
        description = request.data.get('description', '')
        priority = request.data.get('priority', 'medium')
        queue_id = request.data.get('queue', conversation.queue_id)

        if not queue_id:
            return Response(
                {'detail': 'A queue must be selected to create a ticket.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not self._accessible_queues().filter(id=queue_id).exists():
            raise PermissionDenied('You cannot create a ticket in that queue.')

        ticket = Ticket.objects.create(
            queue_id=queue_id,
            title=title,
            description=description,
            priority=priority,
            status='in_progress',
            assigned_to=request.user,
            customer_email=customer.email if customer else '',
            conversation=conversation,
        )
        from csm.services.sla import recalculate_ticket_sla
        recalculate_ticket_sla(ticket)
        if ticket.first_response_due is not None or ticket.resolution_due is not None:
            ticket.save(update_fields=['first_response_due', 'resolution_due'])

        # Ensure the conversation is active (mirrors the claim flow).
        if conversation.status in ('pending',):
            Conversation.objects.filter(id=conversation.id).update(status='active')
            conversation.refresh_from_db()

        # Post a system message in the conversation thread
        system_msg = ConversationMessage.objects.create(
            conversation=conversation,
            sender_type='system',
            content=f'Ticket #{ticket.id} created: {ticket.title}',
        )

        payload = ConversationMessageSerializer(system_msg).data
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'csm_conversation_{conversation.id}',
            {'type': 'conversation.message', 'message': payload},
        )
        # Broadcast the updated conversation so all agents' lists (and the active
        # workspace) pick up the newly linked ticket and unlock the composer.
        conversation.refresh_from_db()
        async_to_sync(channel_layer.group_send)(
            'csm_new_conversations',
            {'type': 'conversation.updated', 'conversation': ConversationSerializer(conversation).data},
        )

        # AC5: deliver the channel's configured confirmation message to the customer.
        _send_ticket_confirmation(conversation)

        return Response(
            {
                'ticket': TicketSerializer(ticket).data,
                'system_message': payload,
            },
            status=status.HTTP_201_CREATED,
        )


class CsmNotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Notification endpoints for the current user.

    - GET  /notifications/           — list my notifications
    - GET  /notifications/{id}/      — retrieve single notification
    - GET  /notifications/unread_count/ — count of unread notifications
    - POST /notifications/{id}/mark_read/ — mark as read
    - POST /notifications/{id}/accept/   — accept org invitation
    - POST /notifications/{id}/decline/  — decline org invitation
    """
    serializer_class = CsmNotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return CsmNotification.objects.filter(
            recipient=self.request.user,
        ).select_related('sender', 'organisation').order_by('-created_at')

    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        count = CsmNotification.objects.filter(
            recipient=request.user,
            is_read=False,
        ).count()
        return Response({'count': count})

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.is_read = True
        notification.save(update_fields=['is_read'])
        return Response(CsmNotificationSerializer(notification).data)

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        notification = self.get_object()
        if notification.notification_type != 'org_invitation':
            return Response(
                {'detail': 'This notification cannot be accepted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if notification.action_status != 'pending':
            return Response(
                {'detail': f'Invitation already {notification.action_status}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_type = notification.metadata.get('user_type', 'agent')
        org_id = notification.metadata.get('organisation_id') or (
            notification.organisation_id
        )

        # Create CustomerUser record
        CustomerUser.objects.get_or_create(
            user=request.user,
            organisation_id=org_id,
            defaults={
                'user_type': user_type,
                'is_active': True,
            },
        )

        notification.action_status = 'accepted'
        notification.is_read = True
        notification.save(update_fields=['action_status', 'is_read'])
        return Response(CsmNotificationSerializer(notification).data)

    @action(detail=True, methods=['post'])
    def decline(self, request, pk=None):
        notification = self.get_object()
        if notification.notification_type != 'org_invitation':
            return Response(
                {'detail': 'This notification cannot be declined.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if notification.action_status != 'pending':
            return Response(
                {'detail': f'Invitation already {notification.action_status}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        notification.action_status = 'declined'
        notification.is_read = True
        notification.save(update_fields=['action_status', 'is_read'])
        return Response(CsmNotificationSerializer(notification).data)


class QuickReplyTemplateViewSet(SlugLookupViewSetMixin, viewsets.ModelViewSet):
    """
    CRUD for quick-reply templates scoped to a CSM organisation.

    - GET    /templates/?organisation={id}   list active templates
    - POST   /templates/                     create
    - GET    /templates/{slug}/                retrieve
    - PATCH  /templates/{slug}/               update
    - DELETE /templates/{slug}/               delete (soft-delete via is_active=False)

    Agents can list/read; only supervisors/admins can create/update/delete.
    """
    serializer_class = QuickReplyTemplateSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        from core.admin_utils import get_csm_admin_org_ids

        qs = QuickReplyTemplate.objects.filter(is_active=True).select_related('created_by', 'team')

        org_id = self.request.query_params.get('organisation')
        if org_id:
            qs = qs.filter(organisation_id=org_id)
        else:
            # Fall back to all orgs the user has access to
            accessible_org_ids = get_csm_admin_org_ids(self.request.user)
            # Also include orgs the user is an agent of
            agent_org_ids = CustomerUser.objects.filter(
                user=self.request.user, is_active=True,
            ).values_list('organisation_id', flat=True)
            all_org_ids = set(list(accessible_org_ids) + list(agent_org_ids))
            qs = qs.filter(organisation_id__in=all_org_ids)

        # Team scoping: show workspace-wide templates (no team) OR templates whose
        # team the user belongs to. In CSM a user's team membership is recorded on
        # CustomerUser.team (core.TeamMember is not populated for CSM agents), so
        # that is the source of truth here. CSM admins may preview another team's
        # view with ?view_as_team= (routing & template sandbox, CSM-S03-05).
        view_as_team = self.request.query_params.get('view_as_team')
        if view_as_team is not None:
            team_ids = self._preview_team_ids(org_id, view_as_team)
        else:
            team_ids = list(CustomerUser.objects.filter(
                user=self.request.user, is_active=True, team__isnull=False,
            ).values_list('team_id', flat=True))
        qs = qs.filter(Q(team__isnull=True) | Q(team_id__in=team_ids))

        # Support filtering by tag
        tag = self.request.query_params.get('tag')
        if tag:
            qs = qs.filter(tags__contains=[tag])

        # Support search by title/content
        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(
                Q(title__icontains=search) | Q(content__icontains=search)
            )

        return qs

    def _admin_organisation(self, org_id):
        """The CustomerOrganisation for ?organisation=, if the user is its CSM admin."""
        from core.admin_utils import get_csm_admin_org_ids
        from customer.models import CustomerOrganisation

        if not org_id or not str(org_id).isdigit():
            raise ValidationError({'organisation': 'This query parameter is required.'})
        if int(org_id) not in set(get_csm_admin_org_ids(self.request.user)):
            raise PermissionDenied('Only CSM admins of this organisation can preview team views.')
        return CustomerOrganisation.objects.get(pk=int(org_id))

    def _preview_team_ids(self, org_id, view_as_team):
        """Team ids for ?view_as_team=<team id>|none, validated against the organisation."""
        organisation = self._admin_organisation(org_id)
        if view_as_team == 'none':
            return []
        if not view_as_team.isdigit() or not Team.objects.filter(
            pk=int(view_as_team), organization_id=organisation.organization_id,
        ).exists():
            raise ValidationError({'view_as_team': 'Team not found for this organisation.'})
        return [int(view_as_team)]

    @action(detail=False, methods=['get'], url_path='preview-teams')
    def preview_teams(self, request):
        """Teams an admin can preview: agents' teams plus teams used by templates."""
        organisation = self._admin_organisation(request.query_params.get('organisation'))
        agent_team_ids = CustomerUser.objects.filter(
            organisation=organisation, is_active=True, team__isnull=False,
        ).values_list('team_id', flat=True)
        template_team_ids = QuickReplyTemplate.objects.filter(
            organisation=organisation, is_active=True, team__isnull=False,
        ).values_list('team_id', flat=True)
        teams = Team.objects.filter(
            Q(pk__in=agent_team_ids) | Q(pk__in=template_team_ids),
        ).order_by('name').values('id', 'name')
        return Response(list(teams))

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        instance = serializer.instance
        # Snapshot the current state BEFORE applying changes
        QuickReplyTemplateHistory.objects.create(
            template=instance,
            edited_by=self.request.user,
            title=instance.title,
            content=instance.content,
            rich_body=instance.rich_body,
            tags=instance.tags,
        )
        serializer.save()

    def perform_destroy(self, instance):
        # Soft delete
        instance.is_active = False
        instance.save(update_fields=['is_active'])

    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        """Return the edit history for a template (admin/supervisor only)."""
        template = self.get_object()
        qs = template.history.select_related('edited_by').order_by('-edited_at')
        serializer = QuickReplyTemplateHistorySerializer(qs, many=True)
        return Response(serializer.data)


class TemplateTagViewSet(viewsets.ModelViewSet):
    """
    Admin-managed tag vocabulary for quick-reply templates.

    - GET    /template-tags/?organisation={id}   list tags (any org member)
    - POST   /template-tags/                     create  (admin/supervisor only)
    - DELETE /template-tags/{id}/                delete  (admin/supervisor only)

    Tags are manageable by administrators only; agents may list them so they
    can apply them when authoring templates.
    """
    serializer_class = TemplateTagSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'delete', 'head', 'options']

    def _user_manages_org(self, user, organisation_id):
        """True if user is an active admin/supervisor of the given org."""
        if not organisation_id:
            return False
        return CustomerUser.objects.filter(
            user=user, is_active=True,
            organisation_id=organisation_id,
            user_type__in=('supervisor', 'admin'),
        ).exists()

    def get_queryset(self):
        qs = TemplateTag.objects.all()
        org_id = self.request.query_params.get('organisation')
        if org_id:
            qs = qs.filter(organisation_id=org_id)
        else:
            # Limit to orgs the user belongs to
            accessible_org_ids = CustomerUser.objects.filter(
                user=self.request.user, is_active=True,
            ).values_list('organisation_id', flat=True)
            qs = qs.filter(organisation_id__in=list(accessible_org_ids))
        return qs

    def perform_create(self, serializer):
        org = serializer.validated_data.get('organisation')
        org_id = org.id if org else None
        if not self._user_manages_org(self.request.user, org_id):
            raise PermissionDenied('Only organisation admins/supervisors may manage template tags.')
        serializer.save()

    def perform_destroy(self, instance):
        if not self._user_manages_org(self.request.user, instance.organisation_id):
            raise PermissionDenied('Only organisation admins/supervisors may manage template tags.')
        instance.delete()


class TicketViewSet(viewsets.ModelViewSet):
    """
    Ticket management for agents.

    - GET    /tickets/                  list (filter: queue, status, assigned_to=me)
    - GET    /tickets/{id}/             retrieve
    - PATCH  /tickets/{id}/             update status/priority/assigned_to
    - POST   /tickets/{id}/claim/       assign to current user, set in_progress
    - POST   /tickets/{id}/close/       set status to closed

    Ordering: ?ordering=priority (Critical first) or ?ordering=-priority (Low first).
    Handled in get_queryset() via CASE expression; OrderingFilter is excluded to
    prevent it from interpreting ?ordering=priority as a plain string sort.
    """
    serializer_class = TicketSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    filter_backends = [DjangoFilterBackend, SearchFilter]

    def get_queryset(self):
        user = self.request.user
        qs = Ticket.objects.select_related('queue', 'assigned_to', 'conversation')

        # Staff/superusers see all
        if not (user.is_staff or user.is_superuser):
            # Filter to queues the user has access to
            admin_org_ids = CustomerUser.objects.filter(
                user=user, is_active=True, user_type__in=('supervisor', 'admin')
            ).values_list('organisation_id', flat=True)
            agent_queue_ids = QueueAgent.objects.filter(user=user).values_list('queue_id', flat=True)
            accessible_queue_ids = Queue.objects.filter(
                Q(organisation_id__in=admin_org_ids) | Q(id__in=agent_queue_ids)
            ).values_list('id', flat=True)
            qs = qs.filter(queue_id__in=accessible_queue_ids)

        # Filter params
        queue_id = self.request.query_params.get('queue')
        if queue_id:
            qs = qs.filter(queue_id=queue_id)

        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        assigned_to = self.request.query_params.get('assigned_to')
        if assigned_to == 'me':
            qs = qs.filter(assigned_to=user)
        elif assigned_to == 'unassigned':
            qs = qs.filter(assigned_to__isnull=True)
        elif assigned_to:
            qs = qs.filter(assigned_to_id=assigned_to)

        # Ordering: ?ordering=priority sorts Critical→Low; default newest first
        ordering = self.request.query_params.get('ordering', '')
        if ordering in ('priority', '-priority'):
            qs = qs.annotate(
                _priority_rank=Case(
                    When(priority='critical', then=Value(0)),
                    When(priority='high',     then=Value(1)),
                    When(priority='medium',   then=Value(2)),
                    When(priority='low',      then=Value(3)),
                    default=Value(4),
                    output_field=IntegerField(),
                )
            )
            if ordering == '-priority':
                qs = qs.order_by('-_priority_rank', '-created_at')
            else:
                qs = qs.order_by('_priority_rank', '-created_at')
        else:
            qs = qs.order_by('-created_at')

        return qs

    def perform_create(self, serializer):
        from csm.services.sla import recalculate_ticket_sla
        ticket = serializer.save()
        recalculate_ticket_sla(ticket)
        if ticket.first_response_due is not None or ticket.resolution_due is not None:
            ticket.save(update_fields=['first_response_due', 'resolution_due'])

    @action(detail=True, methods=['post'])
    def claim(self, request, pk=None):
        """Assign ticket to current user and set status to in_progress."""
        from csm.services.status_machine import assert_transition_allowed
        ticket = self.get_object()
        ticket.assigned_to = request.user
        update_fields = ['assigned_to']
        if ticket.status != 'in_progress':
            try:
                assert_transition_allowed(ticket, 'in_progress')
            except DjangoValidationError as exc:
                _raise_drf_validation(exc)
            ticket.status = 'in_progress'
            update_fields.append('status')
        ticket.save(update_fields=update_fields)
        return Response(TicketSerializer(ticket).data)

    def partial_update(self, request, *args, **kwargs):
        """Override PATCH to enforce the state machine and sync SLA on priority change."""
        from csm.services.sla import recalculate_ticket_sla
        from csm.services.status_machine import assert_transition_allowed
        ticket = self.get_object()
        old_priority = ticket.priority
        new_status = request.data.get('status')
        new_priority = request.data.get('priority')

        # Enforce the state machine before persisting.
        if new_status and new_status != ticket.status:
            try:
                assert_transition_allowed(ticket, new_status)
            except DjangoValidationError as exc:
                _raise_drf_validation(exc)

        # pending_since is stamped/cleared centrally in Ticket.save().
        # No customer notification on manual status change: we only notify on
        # *auto*-resolution (see tasks.py). Ticket and conversation lifecycles
        # stay independent.
        response = super().partial_update(request, *args, **kwargs)

        # Recalculate SLA when priority changes, using now() so the countdown
        # restarts from the moment of the change rather than ticket creation.
        if new_priority and old_priority != new_priority:
            from django.utils import timezone as tz
            ticket.refresh_from_db()
            recalculate_ticket_sla(ticket, base_time=tz.now())
            ticket.save(update_fields=['first_response_due', 'resolution_due'])
            return Response(TicketSerializer(ticket).data)

        return response

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        """Close a ticket."""
        from csm.services.status_machine import assert_transition_allowed
        ticket = self.get_object()
        if ticket.status != 'closed':
            try:
                assert_transition_allowed(ticket, 'closed')
            except DjangoValidationError as exc:
                _raise_drf_validation(exc)
        ticket.status = 'closed'
        ticket.save(update_fields=['status'])

        # No customer notification on close: closing has no such requirement.
        return Response(TicketSerializer(ticket).data)


class TicketFormViewSet(SlugLookupViewSetMixin, ProjectScopedViewSetMixin, viewsets.ModelViewSet):
    # Slug-only lookups; numeric path segments return 404.
    """
    Admin ticket form builder API.

    Permissions: IsAuthenticated + IsProjectMember (project-scoped, not CSM admin).
    List/create require ?project={id}.
    """
    permission_classes = [IsAuthenticated, IsProjectMember]
    http_method_names = ['get', 'post', 'patch', 'put', 'delete']

    def get_queryset(self):
        qs = TicketForm.objects.filter(is_active=True).annotate(
            assignment_count=Count('assignments'),
        ).prefetch_related('fields')
        if self.action == 'list':
            project_id = self.get_required_project_id()
            return qs.filter(project_id=project_id)
        return self.filter_by_accessible_projects(qs)

    def get_serializer_class(self):
        if self.action == 'list':
            return TicketFormListSerializer
        if self.action == 'create':
            return TicketFormCreateSerializer
        if self.action == 'bulk_fields':
            return BulkFieldsSerializer
        return TicketFormDetailSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        form = self.get_queryset().get(pk=serializer.instance.pk)
        return Response(
            TicketFormDetailSerializer(form).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_create(self, serializer):
        project_id = self.get_required_project_id()
        form = serializer.save(
            project_id=project_id,
            created_by=self.request.user,
        )
        ensure_system_fields(form)

    def perform_destroy(self, instance):
        assert_can_delete_form(instance)
        instance.is_active = False
        instance.save(update_fields=['is_active', 'updated_at'])

    @action(detail=True, methods=['put'], url_path='fields')
    def bulk_fields(self, request, pk=None):
        form = self.get_object()
        serializer = BulkFieldsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            bulk_update_fields(form, serializer.validated_data['fields'])
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)
        form = self.get_queryset().get(pk=form.pk)
        return Response(TicketFormDetailSerializer(form).data)

    @action(detail=True, methods=['post'], url_path='set-default')
    def set_default(self, request, pk=None):
        form = self.get_object()
        set_default_form(form)
        form = self.get_queryset().get(pk=form.pk)
        return Response(TicketFormDetailSerializer(form).data)

    @action(detail=True, methods=['get', 'put'], url_path='assignments')
    def assignments(self, request, pk=None):
        form = self.get_object()
        if request.method == 'GET':
            qs = TicketFormAssignment.objects.filter(form=form).select_related(
                'experience_group', 'support_project',
            )
            return Response(TicketFormAssignmentSerializer(qs, many=True).data)

        serializer = ReplaceAssignmentsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            replace_assignments(
                form,
                experience_group_ids=serializer.validated_data.get('experience_group_ids', []),
                support_project_ids=serializer.validated_data.get('support_project_ids', []),
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)

        qs = TicketFormAssignment.objects.filter(form=form).select_related(
            'experience_group', 'support_project',
        )
        return Response(TicketFormAssignmentSerializer(qs, many=True).data)


class SupportProjectViewSet(ProjectScopedViewSetMixin, viewsets.ModelViewSet):
    """
    Support project CRUD (CSM-S01-08).

    List/create require ?project={id}. DELETE soft-archives the row.
    """
    serializer_class = SupportProjectSerializer
    permission_classes = [IsAuthenticated, IsProjectMember]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        qs = SupportProject.objects.select_related('default_queue')
        if self.action == 'list':
            project_id = self.get_required_project_id()
            include_archived = self.request.query_params.get('include_archived') in (
                '1', 'true', 'True',
            )
            return list_support_projects_for_workspace(
                project_id,
                include_archived=include_archived,
            )
        return self.filter_by_accessible_projects(qs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        default_queue = data.get('default_queue')
        try:
            instance = create_support_project(
                project_id=self.get_required_project_id(),
                name=data['name'],
                default_queue_id=default_queue.pk if default_queue else None,
            )
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        instance = self.get_queryset().model.objects.select_related(
            'default_queue',
        ).get(pk=instance.pk)
        return Response(
            SupportProjectSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        kwargs_update = {}
        if 'name' in data:
            kwargs_update['name'] = data['name']
        if 'default_queue' in data:
            queue = data['default_queue']
            kwargs_update['default_queue_id'] = queue.pk if queue else None
        if 'is_archived' in data:
            kwargs_update['is_archived'] = data['is_archived']
        try:
            instance = update_support_project(instance, **kwargs_update)
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        instance = SupportProject.objects.select_related('default_queue').get(pk=instance.pk)
        return Response(SupportProjectSerializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        try:
            instance = archive_support_project(instance)
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        instance = SupportProject.objects.select_related('default_queue').get(pk=instance.pk)
        return Response(SupportProjectSerializer(instance).data)


class CsmWorkTypeViewSet(ProjectScopedViewSetMixin, viewsets.ModelViewSet):
    """
    Work type CRUD (CSM-S01-08).

    List/create require ?project={id}. DELETE deactivates the row.
    """
    serializer_class = CsmWorkTypeSerializer
    permission_classes = [IsAuthenticated, IsProjectMember]
    http_method_names = ['get', 'post', 'patch', 'put', 'delete', 'head', 'options']

    def get_queryset(self):
        qs = CsmWorkType.objects.all()
        if self.action == 'list':
            project_id = self.get_required_project_id()
            include_inactive = self.request.query_params.get('include_inactive') in (
                '1', 'true', 'True',
            )
            return list_work_types_for_workspace(
                project_id,
                include_inactive=include_inactive,
            )
        return self.filter_by_accessible_projects(qs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            instance = create_work_type(
                project_id=self.get_required_project_id(),
                name=data['name'],
                sort_order=data.get('sort_order'),
            )
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        return Response(
            CsmWorkTypeSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        kwargs_update = {key: data[key] for key in ('name', 'sort_order', 'is_active') if key in data}
        try:
            instance = update_work_type(instance, **kwargs_update)
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        return Response(CsmWorkTypeSerializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        try:
            instance = deactivate_work_type(instance)
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        return Response(CsmWorkTypeSerializer(instance).data)

    @action(detail=False, methods=['put'], url_path='reorder')
    def reorder(self, request):
        project_id = self.get_required_project_id()
        serializer = WorkTypeReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            rows = reorder_work_types(project_id, serializer.validated_data['ids'])
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        return Response(CsmWorkTypeSerializer(rows, many=True).data)


class RoutingRuleViewSet(ProjectScopedViewSetMixin, viewsets.ModelViewSet):
    """
    Routing rule CRUD per experience group (CSM-S03-05).

    - GET    /routing-rules/?project={id}&experience_group={id}   ordered list
    - POST   /routing-rules/?project={id}                          create (appended)
    - PATCH  /routing-rules/{id}/                                  update
    - DELETE /routing-rules/{id}/                                  delete
    - PUT    /routing-rules/reorder/?project={id}                  reorder a group
    - GET    /routing-rules/vocabulary/                            condition vocabulary
    """
    serializer_class = RoutingRuleSerializer
    permission_classes = [IsAuthenticated, IsProjectMember, IsCsmAccessAllowed]
    http_method_names = ['get', 'post', 'patch', 'put', 'delete', 'head', 'options']
    pagination_class = None

    def _project(self):
        from core.models import Project
        return Project.objects.get(pk=self.get_required_project_id())

    def get_queryset(self):
        if self.action == 'list':
            project_id = self.get_required_project_id()
            raw_group = self.request.query_params.get('experience_group')
            group_id = int(raw_group) if raw_group and raw_group.isdigit() else None
            return list_rules(project_id, group_id)
        return self.filter_by_accessible_projects(
            RoutingRule.objects.select_related('target_queue', 'project'),
        )

    def create(self, request, *args, **kwargs):
        project = self._project()
        serializer = RoutingRuleWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            rule = create_rule(
                project,
                user=request.user,
                experience_group=data['experience_group'],
                name=data['name'],
                target_queue=data['target_queue'],
                conditions=data.get('conditions', []),
                match_mode=data.get('match_mode', RoutingRule.MatchMode.ALL),
                is_enabled=data.get('is_enabled', True),
                add_tags=data.get('add_tags', []),
            )
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        return Response(RoutingRuleSerializer(rule).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        rule = self.get_object()
        serializer = RoutingRuleWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data.pop('experience_group', None)  # a rule never moves between groups
        try:
            rule = update_rule(rule, **data)
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        return Response(RoutingRuleSerializer(rule).data)

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        delete_rule(self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['put'], url_path='reorder')
    def reorder(self, request):
        project_id = self.get_required_project_id()
        serializer = RoutingRuleReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            rules = reorder_rules(
                project_id,
                serializer.validated_data['experience_group'],
                serializer.validated_data['ids'],
            )
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        return Response(RoutingRuleSerializer(rules, many=True).data)

    @action(detail=False, methods=['get'], url_path='vocabulary')
    def vocabulary(self, request):
        return Response(vocabulary_payload())


class RoutingSandboxViewSet(ProjectScopedViewSetMixin, viewsets.ViewSet):
    """
    POST /routing-sandbox/evaluate/?project={id}

    Evaluates the experience group's current routing rules against a simulated
    conversation and returns the trace. Stateless and read-only: the client
    holds the simulated conversation, and nothing is persisted or broadcast.
    """
    permission_classes = [IsAuthenticated, IsProjectMember, IsCsmAccessAllowed]

    @action(detail=False, methods=['post'], url_path='evaluate')
    def evaluate(self, request):
        from core.models import Project
        project = Project.objects.get(pk=self.get_required_project_id())
        serializer = RoutingSandboxRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            result = run_sandbox(
                project,
                experience_group_id=data['experience_group'],
                messages=data['messages'],
                subject=data.get('subject', ''),
                support_channel_id=data.get('support_channel'),
                customer_organisation_id=data.get('customer_organisation'),
                simulated_at=data.get('simulated_at'),
                evaluate_each_prefix=data.get('evaluate_each_prefix', False),
            )
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        return Response(result)


class SupportChannelViewSet(ProjectScopedViewSetMixin, viewsets.ModelViewSet):
    """
    Support channel CRUD (CSM-S01-02).

    List/create require ?project={id}. DELETE soft-deactivates the row.
    """
    permission_classes = [IsAuthenticated, IsProjectMember]
    http_method_names = ['get', 'post', 'patch', 'delete', 'put', 'head', 'options']

    def get_queryset(self):
        if self.action == 'list':
            project_id = self.get_required_project_id()
            include_inactive = self.request.query_params.get('include_inactive') in (
                '1', 'true', 'True',
            )
            return list_channels_for_project(
                project_id,
                include_inactive=include_inactive,
            )
        return self.filter_by_accessible_projects(channel_detail_queryset())

    def get_serializer_class(self):
        if self.action == 'list':
            return SupportChannelListSerializer
        if self.action in ('create', 'partial_update', 'update'):
            return SupportChannelCreateUpdateSerializer
        return SupportChannelDetailSerializer

    def _service_kwargs(self, data):
        kwargs = {}
        for key in (
            'channel_type', 'display_name', 'welcome_message',
            'ticket_confirmation_message', 'operating_hours',
            'timezone', 'offline_fallback_message', 'offline_alternative',
            'offline_alternative_target_id', 'email_address', 'sort_order', 'is_active',
        ):
            if key in data:
                kwargs[key] = data[key]
        if 'default_queue' in data:
            kwargs['default_queue'] = data['default_queue']
        if 'ticket_form' in data:
            kwargs['ticket_form'] = data['ticket_form']
        return kwargs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            extra_kwargs = {
                k: v for k, v in self._service_kwargs(data).items()
                if k not in ('channel_type', 'display_name')
            }
            instance = create_support_channel(
                project_id=self.get_required_project_id(),
                channel_type=data['channel_type'],
                display_name=data['display_name'],
                **extra_kwargs,
            )
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        instance = channel_detail_queryset().get(pk=instance.pk)
        return Response(
            SupportChannelDetailSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            instance = update_support_channel(
                instance,
                **self._service_kwargs(serializer.validated_data),
            )
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        instance = channel_detail_queryset().get(pk=instance.pk)
        return Response(SupportChannelDetailSerializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        deactivate_support_channel(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['put'], url_path='experience-groups')
    def experience_groups(self, request, pk=None):
        channel = self.get_object()
        serializer = ReplaceChannelAssignmentsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            groups = replace_experience_group_assignments(
                channel,
                serializer.validated_data.get('experience_group_ids', []),
            )
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        return Response(groups)

    @action(detail=True, methods=['get'], url_path='embed-snippet')
    def embed_snippet(self, request, pk=None):
        channel = self.get_object()
        if channel.channel_type != SupportChannel.ChannelType.LIVE_CHAT:
            raise ValidationError({
                'detail': 'Embed snippet is only available for live chat channels.',
            })
        snippet = build_embed_snippet(
            channel,
            base_url=request.build_absolute_uri('/'),
        )
        return Response({
            'snippet': snippet,
            'embed_key': str(channel.embed_key),
        })


# ---------------------------------------------------------------------------
# SLA Policy (MED-218)
# ---------------------------------------------------------------------------

_SLA_DEFAULT_TARGETS = [
    ('critical', 60,   240),   # 1h first response, 4h resolution
    ('high',     240,  480),   # 4h / 8h
    ('medium',   480,  1440),  # 8h / 24h
    ('low',      1440, 2880),  # 24h / 48h
]


class SLAPolicyViewSet(ProjectScopedViewSetMixin, viewsets.ModelViewSet):
    """
    SLA Policy admin API (MED-218).

    - GET   /sla-policy/?project={id}  retrieve the project's SLA policy
    - PUT   /sla-policy/{id}/          full update (replaces all priority targets)
    - PATCH /sla-policy/{id}/          partial update
    """
    serializer_class = SLAPolicySerializer
    permission_classes = [IsAuthenticated, IsCsmAccessAllowed]
    http_method_names = ['get', 'put', 'patch', 'head', 'options']

    def get_queryset(self):
        qs = SLAPolicy.objects.prefetch_related('priority_targets').select_related('project')
        return self.filter_by_accessible_projects(qs)

    def list(self, request, *args, **kwargs):
        """Return (or lazily create) the project's SLA policy."""
        project_id = self.get_required_project_id()
        # Prefer the project's default policy; fall back to any existing one,
        # else lazily create the default so the project always has a fallback.
        policy = (
            SLAPolicy.objects.filter(project_id=project_id, is_default=True).first()
            or SLAPolicy.objects.filter(project_id=project_id).first()
        )
        created = policy is None
        if created:
            policy = SLAPolicy.objects.create(
                project_id=project_id, name='Default SLA Policy', is_default=True,
            )
        if created or not policy.priority_targets.exists():
            existing_priorities = set(
                policy.priority_targets.values_list('priority', flat=True)
            )
            for priority, fr, res in _SLA_DEFAULT_TARGETS:
                if priority not in existing_priorities:
                    SLAPriorityTarget.objects.create(
                        policy=policy,
                        priority=priority,
                        first_response_minutes=fr,
                        resolution_minutes=res,
                    )
            policy.refresh_from_db()
        return Response(SLAPolicySerializer(policy).data)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        was_active = instance.is_active
        old_targets_by_priority = {
            t.priority: (t.first_response_minutes, t.resolution_minutes)
            for t in instance.priority_targets.all()
        }
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        instance.refresh_from_db()
        policy_reactivated = not was_active and instance.is_active

        # Deactivating leaves the stored due dates untouched: get_sla_status reads
        # them as no-SLA while the policy is inactive, and they resume unchanged
        # when it is switched back on. Only recompute while the policy is active
        # (target edits, reactivation) — the anchor is preserved either way.
        if instance.is_active:
            from csm.services.sla import recalculate_ticket_sla_after_policy_change
            project_id = instance.project_id
            open_tickets = list(
                Ticket.objects
                .filter(queue__project_id=project_id, status__in=('todo', 'in_progress'))
                .select_related('queue__project')
            )
            for ticket in open_tickets:
                recalculate_ticket_sla_after_policy_change(
                    ticket,
                    old_targets_by_priority,
                    policy_reactivated=policy_reactivated,
                )
            if open_tickets:
                Ticket.objects.bulk_update(open_tickets, ['first_response_due', 'resolution_due'])

        return Response(serializer.data)


class BusinessHoursCalendarViewSet(ProjectScopedViewSetMixin, viewsets.ModelViewSet):
    """Business-hours calendar CRUD.

    List/create require ?project={id}. Deleting a calendar detaches it from any
    policies (SET_NULL); those policies fall back to wall-clock countdowns.
    """
    serializer_class = BusinessHoursCalendarSerializer
    permission_classes = [IsAuthenticated, IsProjectMember]
    http_method_names = ['get', 'post', 'patch', 'put', 'delete', 'head', 'options']

    def get_queryset(self):
        if self.action == 'list':
            project_id = self.get_required_project_id()
            return BusinessHoursCalendar.objects.filter(project_id=project_id)
        return self.filter_by_accessible_projects(BusinessHoursCalendar.objects.all())

    def perform_create(self, serializer):
        serializer.save(project_id=self.get_required_project_id())


# --- Status machine admin config ------------------------------------------

class TicketStatusViewSet(ProjectScopedViewSetMixin, viewsets.ModelViewSet):
    """CRUD for a project's ticket statuses (CSM-S02-03).

    - GET    /ticket-statuses/?project={id}   list (seeds built-ins on first read)
    - POST   /ticket-statuses/?project={id}   create custom status at a position
    - PATCH  /ticket-statuses/{id}/           rename / recolour / (de)activate
    - DELETE /ticket-statuses/{id}/           delete custom status (see below)

    Built-in statuses cannot be deleted. Deleting a status still in use on
    tickets requires ?confirm=true (the UI warns first).
    """
    serializer_class = TicketStatusSerializer
    permission_classes = [IsAuthenticated, IsProjectMember]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        from csm.services.status_machine import ensure_status_machine
        if self.action == 'list':
            project_id = self.get_required_project_id()
            ensure_status_machine(project_id)
            return TicketStatus.objects.filter(project_id=project_id)
        return TicketStatus.objects.filter(
            project_id__in=self._accessible_project_ids(),
        )

    def create(self, request, *args, **kwargs):
        from csm.services.status_machine import insert_custom_status
        project_id = self.get_required_project_id()
        serializer = TicketStatusCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            instance = insert_custom_status(
                project_id=project_id,
                name=data['name'],
                color=data.get('color'),
                position=data.get('position', 0),
            )
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        return Response(
            TicketStatusSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        from csm.services.status_machine import tickets_using_status
        instance = self.get_object()
        if instance.is_builtin:
            raise ValidationError(
                {'detail': 'Built-in statuses cannot be deleted.'}
            )
        in_use = tickets_using_status(instance.project_id, instance.slug).count()
        confirmed = request.query_params.get('confirm') in ('1', 'true', 'True')
        if in_use and not confirmed:
            raise ValidationError({
                'detail': (
                    f'{in_use} ticket(s) currently use this status. '
                    'Retry with ?confirm=true to delete anyway.'
                ),
                'tickets_in_use': in_use,
            })
        # Reassign any tickets still on this status to 'in_progress' (a built-in
        # that can't be deleted) so they aren't stranded on a slug that no longer
        # exists — matches what the delete warning promises.
        tickets_using_status(instance.project_id, instance.slug).update(status='in_progress')
        # Drop transitions referencing the deleted slug so the machine stays clean.
        TicketStatusTransition.objects.filter(
            Q(project_id=instance.project_id),
            Q(from_status=instance.slug) | Q(to_status=instance.slug),
        ).delete()
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class StatusMachineView(ProjectScopedViewSetMixin, viewsets.ViewSet):
    """The whole status machine for a project, for the matrix-style admin UI.

    - GET  /ticket-status-machine/?project={id}   statuses + transitions + auto-resolve
    - PUT  /ticket-status-machine/?project={id}   replace transition set
    - PATCH/ticket-status-machine/auto-resolve/   update the auto-resolution rule
    """
    permission_classes = [IsAuthenticated, IsProjectMember]

    def list(self, request):
        from csm.services.status_machine import (
            get_statuses, get_transitions, get_auto_resolve_config,
        )
        project_id = self.get_required_project_id()
        data = StatusMachineSerializer({
            'statuses': get_statuses(project_id).filter(is_active=True),
            'transitions': get_transitions(project_id),
            'auto_resolve': get_auto_resolve_config(project_id),
        }).data
        return Response(data)

    def update(self, request, *args, **kwargs):
        """Bulk-replace the permitted transitions."""
        from csm.services.status_machine import ensure_status_machine
        project_id = self.get_required_project_id()
        ensure_status_machine(project_id)
        serializer = ReplaceTransitionsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        valid_slugs = set(
            TicketStatus.objects.filter(project_id=project_id)
            .values_list('slug', flat=True)
        )
        edges = []
        for edge in serializer.validated_data['transitions']:
            frm, to = edge.get('from_status'), edge.get('to_status')
            if frm not in valid_slugs or to not in valid_slugs:
                raise ValidationError(
                    {'transitions': f'Unknown status in transition {frm} -> {to}.'}
                )
            edges.append((frm, to))
        TicketStatusTransition.objects.filter(project_id=project_id).delete()
        TicketStatusTransition.objects.bulk_create([
            TicketStatusTransition(project_id=project_id, from_status=f, to_status=t)
            for f, t in set(edges)
        ])
        return self.list(request)

    @action(detail=False, methods=['patch'], url_path='auto-resolve')
    def auto_resolve(self, request):
        from csm.services.status_machine import get_auto_resolve_config
        project_id = self.get_required_project_id()
        config = get_auto_resolve_config(project_id)
        serializer = TicketAutoResolveConfigSerializer(
            config, data=request.data, partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

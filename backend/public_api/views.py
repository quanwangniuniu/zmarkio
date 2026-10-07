"""Public REST API under /api/v1/csm/, for external systems holding an API key or OAuth client."""

from datetime import timezone as dt_timezone

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from csm.models import QuickReplyTemplateHistory, RoutingRule
from csm.serializers import RoutingRuleReorderSerializer, RoutingRuleSerializer
from csm.services.routing_rules import create_rule, reorder_rules, update_rule
from csm.services.sla import recalculate_ticket_sla
from csm.services.status_machine import assert_transition_allowed
from public_api import scoping
from public_api.authentication import ApiKeyAuthentication, OAuthClientAuthentication
from public_api.permissions import HasApiScope
from public_api.serializers import (
    PublicAgentSerializer,
    PublicConversationSerializer,
    PublicCustomerOrganisationSerializer,
    PublicCustomerSerializer,
    PublicMessageSerializer,
    PublicQueueSerializer,
    PublicRoutingRuleWriteSerializer,
    PublicTemplateSerializer,
    PublicTicketSerializer,
)
from public_api.throttling import ApiCredentialRateThrottle


def _raise_drf_validation(exc):
    raise ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)


class PublicApiPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class PublicApiMixin:
    # No session or JWT: a browser session must never reach these endpoints, and
    # without SessionAuthentication there is no CSRF check to trip over.
    authentication_classes = [ApiKeyAuthentication, OAuthClientAuthentication]
    permission_classes = [IsAuthenticated, HasApiScope]
    throttle_classes = [ApiCredentialRateThrottle]
    pagination_class = PublicApiPagination

    @property
    def principal(self):
        return self.request.user

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['principal'] = self.principal
        return context


class WhoAmIView(PublicApiMixin, APIView):
    """GET /api/v1/csm/whoami/: the workspace, project and scopes of the calling credential."""

    def get(self, request):
        principal = self.principal
        return Response({
            'credential_type': principal.kind,
            'name': principal.name,
            'organization': {'id': principal.organization_id, 'slug': principal.organization_slug},
            'project_id': principal.project_id,
            'scopes': principal.scopes,
        })


class SoftDeleteMixin:
    """DELETE deactivates instead of removing, matching the internal API."""

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=['is_active', 'updated_at'])


class QueueViewSet(PublicApiMixin, SoftDeleteMixin, viewsets.ModelViewSet):
    api_resource = 'queues'
    serializer_class = PublicQueueSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        return scoping.queues(self.principal).select_related('organisation').order_by('display_order', 'id')

    def perform_create(self, serializer):
        serializer.save(project_id=self.principal.project_id)


class TicketViewSet(PublicApiMixin, viewsets.ModelViewSet):
    """Filters: ?queue=, ?status=, ?updated_since=<ISO 8601>. No DELETE, as in the product."""

    api_resource = 'tickets'
    serializer_class = PublicTicketSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        qs = scoping.tickets(self.principal).select_related('queue', 'assigned_to', 'conversation')
        params = self.request.query_params
        if params.get('queue'):
            qs = qs.filter(queue_id=params['queue'])
        if params.get('status'):
            qs = qs.filter(status=params['status'])
        return _updated_since(qs, params).order_by('-created_at', '-id')

    def perform_create(self, serializer):
        ticket = serializer.save()
        recalculate_ticket_sla(ticket)
        if ticket.first_response_due is not None or ticket.resolution_due is not None:
            ticket.save(update_fields=['first_response_due', 'resolution_due'])

    def perform_update(self, serializer):
        ticket = serializer.instance
        old_priority = ticket.priority
        new_status = serializer.validated_data.get('status')
        if new_status and new_status != ticket.status:
            try:
                assert_transition_allowed(ticket, new_status)
            except DjangoValidationError as exc:
                _raise_drf_validation(exc)
        ticket = serializer.save()
        if ticket.priority != old_priority:
            recalculate_ticket_sla(ticket, base_time=timezone.now())
            ticket.save(update_fields=['first_response_due', 'resolution_due'])


class ConversationViewSet(PublicApiMixin, viewsets.ModelViewSet):
    """Filters: ?queue=, ?status=, ?updated_since=. Messages are read-only."""

    api_resource = 'conversations'
    serializer_class = PublicConversationSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        qs = scoping.conversations(self.principal).select_related('customer', 'queue', 'assigned_to__user')
        params = self.request.query_params
        if params.get('queue'):
            qs = qs.filter(queue_id=params['queue'])
        if params.get('status'):
            qs = qs.filter(status=params['status'])
        return _updated_since(qs, params).order_by('-started_at', '-id')

    @action(detail=True, methods=['get'])
    def messages(self, request, pk=None):
        conversation = self.get_object()
        qs = conversation.messages.select_related('sender_agent__user').order_by('created_at', 'id')
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(
            PublicMessageSerializer(page, many=True, context=self.get_serializer_context()).data,
        )


class CustomerViewSet(PublicApiMixin, viewsets.ModelViewSet):
    """Filter: ?email=."""

    api_resource = 'customers'
    serializer_class = PublicCustomerSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        qs = scoping.customers(self.principal).select_related('experience_group', 'status_label')
        email = self.request.query_params.get('email')
        if email:
            qs = qs.filter(email__iexact=email)
        return qs.order_by('-created_at', '-id')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['project_id'] = self.principal.project_id  # CustomerSerializer's uniqueness check
        return context

    def perform_create(self, serializer):
        serializer.save(project_id=self.principal.project_id, organization_id=self.principal.organization_id)


class CustomerOrganisationViewSet(PublicApiMixin, viewsets.ModelViewSet):
    api_resource = 'organisations'
    serializer_class = PublicCustomerOrganisationSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        return scoping.customer_organisations(self.principal).order_by('name', 'id')

    def perform_create(self, serializer):
        serializer.save(organization_id=self.principal.organization_id)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        count = instance.customers.count()
        if count:
            raise ValidationError({
                'detail': f'Cannot delete: {count} customer(s) belong to this organisation. Reassign them first.',
            })
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TemplateViewSet(PublicApiMixin, SoftDeleteMixin, viewsets.ModelViewSet):
    api_resource = 'templates'
    serializer_class = PublicTemplateSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        return scoping.templates(self.principal).select_related('created_by').order_by('title', 'id')

    def perform_update(self, serializer):
        # Same history snapshot as the product; the editor is an integration, not a person.
        instance = serializer.instance
        QuickReplyTemplateHistory.objects.create(
            template=instance,
            edited_by=None,
            title=instance.title,
            content=instance.content,
            rich_body=instance.rich_body,
            tags=instance.tags,
        )
        serializer.save()


class AgentViewSet(PublicApiMixin, viewsets.ModelViewSet):
    """CSM profiles (agent / supervisor / admin) of existing workspace members."""

    api_resource = 'agents'
    serializer_class = PublicAgentSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        return scoping.agents(self.principal).select_related('user', 'queue', 'organisation').order_by('id')

    def perform_destroy(self, instance):
        if instance.is_creator:
            raise ValidationError({'detail': 'Cannot delete the organisation creator.'})
        instance.delete()


class RoutingRuleViewSet(PublicApiMixin, viewsets.ModelViewSet):
    """
    Writes go through csm.services.routing_rules, which validates the group,
    queue and conditions against the project. Filter: ?experience_group=.
    PUT /routing-rules/reorder/ takes {"experience_group", "ids"}.
    """

    api_resource = 'routing_rules'
    serializer_class = RoutingRuleSerializer
    http_method_names = ['get', 'post', 'patch', 'put', 'delete', 'head', 'options']

    def get_queryset(self):
        qs = scoping.routing_rules(self.principal).select_related('target_queue')
        group = self.request.query_params.get('experience_group')
        if group:
            qs = qs.filter(experience_group_id=group)
        return qs.order_by('experience_group_id', 'position', 'id')

    def get_serializer_class(self):
        if self.action in ('create', 'partial_update'):
            return PublicRoutingRuleWriteSerializer
        return RoutingRuleSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            rule = create_rule(
                self.principal.project_id,
                user=None,
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

    def partial_update(self, request, *args, **kwargs):
        rule = self.get_object()
        serializer = self.get_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data.pop('experience_group', None)  # a rule never moves between groups
        try:
            rule = update_rule(rule, **data)
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        return Response(RoutingRuleSerializer(rule).data)

    def update(self, request, *args, **kwargs):
        if not kwargs.get('partial'):
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        return super().update(request, *args, **kwargs)

    @action(detail=False, methods=['put'], url_path='reorder')
    def reorder(self, request):
        serializer = RoutingRuleReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            rules = reorder_rules(
                self.principal.project_id,
                serializer.validated_data['experience_group'],
                serializer.validated_data['ids'],
            )
        except DjangoValidationError as exc:
            _raise_drf_validation(exc)
        return Response(RoutingRuleSerializer(rules, many=True).data)


def _updated_since(qs, params):
    raw = params.get('updated_since')
    if not raw:
        return qs
    value = parse_datetime(raw)
    if value is None:
        raise ValidationError({'updated_since': 'Use an ISO 8601 date-time.'})
    if timezone.is_naive(value):
        value = timezone.make_aware(value, dt_timezone.utc)
    return qs.filter(updated_at__gte=value)


# Listed for the router in public_api.urls.
RESOURCE_VIEWSETS = (
    ('customers', CustomerViewSet, 'public-api-customer'),
    ('organisations', CustomerOrganisationViewSet, 'public-api-organisation'),
    ('tickets', TicketViewSet, 'public-api-ticket'),
    ('conversations', ConversationViewSet, 'public-api-conversation'),
    ('templates', TemplateViewSet, 'public-api-template'),
    ('routing-rules', RoutingRuleViewSet, 'public-api-routing-rule'),
    ('queues', QueueViewSet, 'public-api-queue'),
    ('agents', AgentViewSet, 'public-api-agent'),
)

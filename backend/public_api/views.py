"""Public REST API under /api/v1/csm/, for external systems holding an API key or OAuth client."""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.fields import get_error_detail
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from csm.models import RoutingRule
from csm.serializers import ConversationMessageSerializer, RoutingRuleReorderSerializer, RoutingRuleSerializer
from csm.services.routing_rules import create_rule, reorder_rules, update_rule
from csm.services.sla import restart_ticket_sla, start_ticket_sla
from csm.services.status_machine import assert_transition_allowed
from public_api.authentication import ApiKeyAuthentication, OAuthClientAuthentication
from public_api.permissions import HasApiScope
from public_api.serializers import (
    PublicAgentSerializer,
    PublicConversationSerializer,
    PublicCustomerOrganisationSerializer,
    PublicCustomerSerializer,
    PublicQueueSerializer,
    PublicRoutingRuleWriteSerializer,
    PublicTemplateSerializer,
    PublicTicketSerializer,
)
from public_api.services import scoping
from public_api.throttles import ApiCredentialRateThrottle


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

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['principal'] = self.request.user
        return context


class SoftDeleteMixin:
    """DELETE deactivates instead of removing, matching the internal API."""

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=['is_active', 'updated_at'])


class QueueViewSet(PublicApiMixin, SoftDeleteMixin, viewsets.ModelViewSet):
    """
    - GET/POST          /api/v1/csm/queues/
    - GET/PATCH/DELETE  /api/v1/csm/queues/{id}/      DELETE deactivates
    """

    api_resource = 'queues'
    serializer_class = PublicQueueSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        return scoping.queues(self.request.user).select_related('organisation').order_by('display_order', 'id')

    def perform_create(self, serializer):
        serializer.save(project_id=self.request.user.project_id)


class TicketViewSet(PublicApiMixin, viewsets.ModelViewSet):
    """
    - GET/POST   /api/v1/csm/tickets/?queue=&status=
    - GET/PATCH  /api/v1/csm/tickets/{id}/     status changes follow the status machine

    No DELETE, as in the product.
    """

    api_resource = 'tickets'
    serializer_class = PublicTicketSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        qs = scoping.tickets(self.request.user).select_related('queue', 'assigned_to', 'conversation')
        params = self.request.query_params
        if params.get('queue'):
            qs = qs.filter(queue_id=params['queue'])
        if params.get('status'):
            qs = qs.filter(status=params['status'])
        return qs.order_by('-created_at', '-id')

    def perform_create(self, serializer):
        start_ticket_sla(serializer.save())

    def perform_update(self, serializer):
        ticket = serializer.instance
        old_priority = ticket.priority
        new_status = serializer.validated_data.get('status')
        if new_status and new_status != ticket.status:
            try:
                assert_transition_allowed(ticket, new_status)
            except DjangoValidationError as exc:
                raise ValidationError(get_error_detail(exc))
        ticket = serializer.save()
        if ticket.priority != old_priority:
            restart_ticket_sla(ticket)


class ConversationViewSet(PublicApiMixin, viewsets.ModelViewSet):
    """
    - GET/POST   /api/v1/csm/conversations/?queue=&status=
    - GET/PATCH  /api/v1/csm/conversations/{id}/
    - GET        /api/v1/csm/conversations/{id}/messages/    read-only, oldest first
    """

    api_resource = 'conversations'
    serializer_class = PublicConversationSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        qs = scoping.conversations(self.request.user).select_related('customer', 'queue', 'assigned_to__user')
        params = self.request.query_params
        if params.get('queue'):
            qs = qs.filter(queue_id=params['queue'])
        if params.get('status'):
            qs = qs.filter(status=params['status'])
        return qs.order_by('-started_at', '-id')

    @action(detail=True, methods=['get'])
    def messages(self, request, pk=None):
        conversation = self.get_object()
        qs = conversation.messages.select_related('sender_agent__user').order_by('created_at', 'id')
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(
            ConversationMessageSerializer(page, many=True, context=self.get_serializer_context()).data,
        )


class CustomerViewSet(PublicApiMixin, viewsets.ModelViewSet):
    """
    - GET/POST          /api/v1/csm/customers/?email=
    - GET/PATCH/DELETE  /api/v1/csm/customers/{id}/
    """

    api_resource = 'customers'
    serializer_class = PublicCustomerSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        qs = scoping.customers(self.request.user).select_related('experience_group', 'status_label')
        email = self.request.query_params.get('email')
        if email:
            qs = qs.filter(email__iexact=email)
        return qs.order_by('-created_at', '-id')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['project_id'] = self.request.user.project_id  # CustomerSerializer's uniqueness check
        return context

    def perform_create(self, serializer):
        principal = self.request.user
        serializer.save(project_id=principal.project_id, organization_id=principal.organization_id)


class CustomerOrganisationViewSet(PublicApiMixin, viewsets.ModelViewSet):
    """
    - GET/POST          /api/v1/csm/organisations/
    - GET/PATCH/DELETE  /api/v1/csm/organisations/{id}/    DELETE refused while it has customers
    """

    api_resource = 'organisations'
    serializer_class = PublicCustomerOrganisationSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        return scoping.customer_organisations(self.request.user).order_by('name', 'id')

    def perform_create(self, serializer):
        serializer.save(organization_id=self.request.user.organization_id)

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
    """
    - GET/POST          /api/v1/csm/templates/
    - GET/PATCH/DELETE  /api/v1/csm/templates/{id}/    DELETE deactivates
    """

    api_resource = 'templates'
    serializer_class = PublicTemplateSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        return scoping.templates(self.request.user).select_related('created_by').order_by('title', 'id')

    def perform_update(self, serializer):
        # The editor is an integration, not a person.
        serializer.instance.record_history(edited_by=None)
        serializer.save()


class AgentViewSet(PublicApiMixin, viewsets.ModelViewSet):
    """
    CSM profiles (agent / supervisor / admin) of existing workspace members.

    - GET/POST          /api/v1/csm/agents/
    - GET/PATCH/DELETE  /api/v1/csm/agents/{id}/    DELETE refused for the organisation's creator
    """

    api_resource = 'agents'
    serializer_class = PublicAgentSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        return scoping.agents(self.request.user).select_related('user', 'queue', 'organisation').order_by('id')

    def perform_destroy(self, instance):
        if instance.is_creator:
            raise ValidationError({'detail': 'Cannot delete the organisation creator.'})
        instance.delete()


class RoutingRuleViewSet(PublicApiMixin, viewsets.ModelViewSet):
    """
    Writes go through csm.services.routing_rules, which validates the group,
    queue and conditions against the project.

    - GET/POST          /api/v1/csm/routing-rules/?experience_group=
    - GET/PATCH/DELETE  /api/v1/csm/routing-rules/{id}/
    - PUT               /api/v1/csm/routing-rules/reorder/    {"experience_group", "ids"}
    """

    api_resource = 'routing_rules'
    serializer_class = RoutingRuleSerializer
    http_method_names = ['get', 'post', 'patch', 'put', 'delete', 'head', 'options']

    def get_queryset(self):
        qs = scoping.routing_rules(self.request.user).select_related('target_queue')
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
                request.user.project_id,
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
            raise ValidationError(get_error_detail(exc))
        return Response(RoutingRuleSerializer(rule).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        if not kwargs.get('partial'):
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        rule = self.get_object()
        serializer = self.get_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data.pop('experience_group', None)  # a rule never moves between groups
        try:
            rule = update_rule(rule, **data)
        except DjangoValidationError as exc:
            raise ValidationError(get_error_detail(exc))
        return Response(RoutingRuleSerializer(rule).data)

    @action(detail=False, methods=['put'], url_path='reorder')
    def reorder(self, request):
        serializer = RoutingRuleReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            rules = reorder_rules(
                request.user.project_id,
                serializer.validated_data['experience_group'],
                serializer.validated_data['ids'],
            )
        except DjangoValidationError as exc:
            raise ValidationError(get_error_detail(exc))
        return Response(RoutingRuleSerializer(rules, many=True).data)

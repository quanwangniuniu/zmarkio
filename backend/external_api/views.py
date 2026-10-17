"""External REST API under /api/v1/csm/: the internal CSM viewsets behind ExternalResourceMixin."""

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from csm.models import CustomerUser
from csm.views import (
    ConversationViewSet,
    CustomerUserViewSet,
    QueueViewSet,
    QuickReplyTemplateViewSet,
    RoutingRuleViewSet,
    TicketViewSet,
)
from customer.views import CustomerOrganisationViewSet, CustomerViewSet
from external_api import services
from external_api.authentication import ApiKeyAuthentication, OAuthClientAuthentication
from external_api.permissions import HasApiScope

User = get_user_model()


class ExternalResourceMixin:
    """
    Subclasses set `api_resource` (scope name), `rows` (visible queryset), `relations`
    ({writable FK: scoped queryset}) and `hidden_fields`.
    """

    authentication_classes = [ApiKeyAuthentication, OAuthClientAuthentication]
    permission_classes = [IsAuthenticated, HasApiScope]
    api_resource = None
    rows = None
    relations = {}
    hidden_fields = ()

    @classmethod
    def get_extra_actions(cls):
        # Internal actions (claim, invite, reorder...) act as a person; only CRUD is external.
        return []

    def get_permissions(self):
        return [permission() for permission in self.permission_classes]

    def get_queryset(self):
        return self.rows(self.request.user)

    def get_object(self):
        # By id, also for resources the internal API looks up by slug.
        return GenericAPIView.get_object(self)

    def get_required_project_id(self):
        # Internal create paths read the project from ?project=; a credential has exactly one.
        return self.request.user.project_id

    def get_serializer_context(self):
        # No request: the internal serializers check access through request.user, which a
        # credential is not. The scoped relations below do that job instead.
        return {'request': None, 'format': self.format_kwarg, 'view': self,
                'project_id': self.request.user.project_id}

    def get_serializer(self, *args, **kwargs):
        serializer = super().get_serializer(*args, **kwargs)
        fields = getattr(serializer, 'child', serializer).fields
        for name, scope in self.relations.items():
            if name in fields and not fields[name].read_only:
                fields[name].queryset = scope(self.request.user)
        for name in self.hidden_fields:
            fields.pop(name, None)
        return serializer


class CustomerResource(ExternalResourceMixin, CustomerViewSet):
    api_resource = 'customers'
    rows = staticmethod(services.scoped_customers)
    relations = {
        'experience_group': services.scoped_experience_groups,
        'region': services.scoped_regions,
        'organisation': services.scoped_customer_organisations,
        'status_label': services.scoped_status_labels,
    }

    def perform_create(self, serializer):
        principal = self.request.user
        serializer.save(project_id=principal.project_id, organization_id=principal.organization_id)


class CustomerOrganisationResource(ExternalResourceMixin, CustomerOrganisationViewSet):
    api_resource = 'organisations'
    rows = staticmethod(services.scoped_customer_organisations)
    relations = {'region': services.scoped_regions, 'organization': services.scoped_workspace}
    hidden_fields = ('customers',)

    def create(self, request, *args, **kwargs):
        # Always the credential's workspace (the name check depends on it).
        serializer = self.get_serializer(data={**request.data, 'organization': request.user.organization_id})
        serializer.is_valid(raise_exception=True)
        # The internal create also makes the caller the organisation's admin; a credential is not a person.
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        count = instance.customers.count()
        if count:
            raise ValidationError({
                'detail': f'Cannot delete: {count} customer(s) belong to this organisation. Reassign them first.',
            })
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TicketResource(ExternalResourceMixin, TicketViewSet):
    api_resource = 'tickets'
    rows = staticmethod(services.scoped_tickets)
    relations = {
        'queue': services.scoped_queues,
        'conversation': services.scoped_conversations,
        'assigned_to': services.scoped_assignable_users,
    }


class ConversationResource(ExternalResourceMixin, ConversationViewSet):
    api_resource = 'conversations'
    rows = staticmethod(services.scoped_conversations)
    relations = {
        'queue': services.scoped_queues,
        'customer': services.scoped_customers,
        'assigned_to': services.scoped_agents,
    }

    def perform_create(self, serializer):
        # A conversation without a queue couldn't be attributed to the workspace.
        if not serializer.validated_data.get('queue'):
            raise ValidationError({'queue': 'This field is required.'})
        serializer.save()


class TemplateResource(ExternalResourceMixin, QuickReplyTemplateViewSet):
    api_resource = 'templates'
    rows = staticmethod(services.scoped_templates)
    relations = {'organisation': services.scoped_customer_organisations}


class QueueResource(ExternalResourceMixin, QueueViewSet):
    api_resource = 'queues'
    rows = staticmethod(services.scoped_queues)
    relations = {
        'organisation': services.scoped_customer_organisations,
        'sla_policy': services.scoped_sla_policies,
        'project': services.scoped_project,
    }

    def perform_create(self, serializer):
        # A queue without a customer organisation couldn't be attributed to the workspace.
        if not serializer.validated_data.get('organisation'):
            raise ValidationError({'organisation': 'This field is required.'})
        serializer.save(project_id=self.request.user.project_id)


class AgentResource(ExternalResourceMixin, CustomerUserViewSet):
    api_resource = 'agents'
    rows = staticmethod(services.scoped_agents)
    relations = {'organisation': services.scoped_customer_organisations, 'queue': services.scoped_queues}

    def perform_create(self, serializer):
        # The internal create signs up unknown emails; an integration may only give a CSM
        # profile to an existing member of its workspace.
        email = serializer.validated_data.pop('email', '')
        user = User.objects.filter(
            email__iexact=email,
            organization_memberships__organization_id=self.request.user.organization_id,
            organization_memberships__is_active=True,
        ).first()
        if user is None:
            raise ValidationError({'email': 'No active member of this workspace has that email.'})
        if CustomerUser.objects.filter(user=user, queue=serializer.validated_data.get('queue')).exists():
            raise ValidationError({'email': 'This person already has a profile for that queue.'})
        serializer.save(user=user)


class RoutingRuleResource(ExternalResourceMixin, RoutingRuleViewSet):
    api_resource = 'routing_rules'
    rows = staticmethod(services.scoped_routing_rules)
    relations = {
        'experience_group': services.scoped_experience_groups,
        'target_queue': services.scoped_queues,
    }

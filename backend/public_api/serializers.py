"""
Public API serializers.

Each one subclasses the internal CSM serializer so the JSON matches what the
product already returns, and swaps every writable relation for a queryset from
public_api.scoping. The internal serializers check access through
`request.user`; a credential has no user, so those checks are replaced here
rather than skipped.
"""

from django.contrib.auth import get_user_model
from rest_framework import serializers

from csm.models import CustomerUser
from csm.serializers import (
    ConversationMessageSerializer,
    ConversationSerializer,
    CustomerUserSerializer,
    QueueSerializer,
    QuickReplyTemplateSerializer,
    RoutingRuleWriteSerializer,
    TicketSerializer,
)
from csm.services.status_machine import get_status
from customer.serializers import CustomerOrganisationSerializer, CustomerSerializer
from public_api import scoping

User = get_user_model()


class ScopedRelationsMixin:
    """Limits each writable relation in `scoped_relations` to what the credential may reference."""

    scoped_relations = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        principal = self.context.get('principal')
        if principal is None:
            return  # read-only use, e.g. a webhook payload
        for name, scope in self.scoped_relations.items():
            field = self.fields.get(name)
            if field is not None and not field.read_only:
                field.queryset = scope(principal)


class PublicQueueSerializer(ScopedRelationsMixin, QueueSerializer):
    scoped_relations = {
        'organisation': scoping.customer_organisations,
        'sla_policy': scoping.sla_policies,
    }

    class Meta(QueueSerializer.Meta):
        read_only_fields = [*QueueSerializer.Meta.read_only_fields, 'project']
        # A queue without a customer organisation can't be attributed to a workspace.
        extra_kwargs = {'organisation': {'required': True, 'allow_null': False}}


class PublicTicketSerializer(ScopedRelationsMixin, TicketSerializer):
    scoped_relations = {
        'queue': scoping.queues,
        'conversation': scoping.conversations,
        'assigned_to': scoping.assignable_users,
    }

    def validate(self, attrs):
        attrs = super().validate(attrs)
        queue = attrs.get('queue') or (self.instance.queue if self.instance else None)
        assignee = attrs.get('assigned_to')
        if assignee is not None and queue is not None and not CustomerUser.objects.filter(
            user=assignee, organisation_id=queue.organisation_id, is_active=True,
        ).exists():
            raise serializers.ValidationError({'assigned_to': "Assignee must be an active CSM user in the queue's organisation."})
        # Transitions on update are enforced by the view; a new ticket may start in any defined status.
        if self.instance is None and 'status' in attrs and get_status(queue.project_id, attrs['status']) is None:
            raise serializers.ValidationError({'status': f"Unknown status '{attrs['status']}'."})
        return attrs


class PublicConversationSerializer(ScopedRelationsMixin, ConversationSerializer):
    scoped_relations = {
        'queue': scoping.queues,
        'customer': scoping.customers,
        'assigned_to': scoping.agents,
    }

    class Meta(ConversationSerializer.Meta):
        # Queue-less conversations are outside every credential's scope.
        extra_kwargs = {'queue': {'required': True, 'allow_null': False}}

    def _validate_queue_access(self, queue):
        """Replaced by the scoped `queue` queryset; the base check reads request.user."""


class PublicMessageSerializer(ConversationMessageSerializer):
    pass


class PublicCustomerSerializer(ScopedRelationsMixin, CustomerSerializer):
    scoped_relations = {
        'experience_group': scoping.experience_groups,
        'region': scoping.regions,
        'organisation': scoping.customer_organisations,
        'status_label': scoping.status_labels,
    }


class PublicCustomerOrganisationSerializer(ScopedRelationsMixin, CustomerOrganisationSerializer):
    scoped_relations = {'region': scoping.regions}

    class Meta(CustomerOrganisationSerializer.Meta):
        # No nested customer list (unbounded); `organization` is the credential's workspace.
        fields = [f for f in CustomerOrganisationSerializer.Meta.fields if f != 'customers']
        read_only_fields = ['id', 'organization', 'created_at', 'updated_at']

    def validate_name(self, value):
        qs = scoping.customer_organisations(self.context['principal']).filter(name__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('An organisation with this name already exists.')
        return value


class PublicTemplateSerializer(ScopedRelationsMixin, QuickReplyTemplateSerializer):
    scoped_relations = {'organisation': scoping.customer_organisations}

    class Meta(QuickReplyTemplateSerializer.Meta):
        # Teams are tenant-scoped and not part of the public API.
        read_only_fields = [*QuickReplyTemplateSerializer.Meta.read_only_fields, 'team']

    def validate_organisation(self, value):
        """Replaced by the scoped `organisation` queryset; the base check reads request.user."""
        return value


class PublicRoutingRuleWriteSerializer(ScopedRelationsMixin, RoutingRuleWriteSerializer):
    scoped_relations = {
        'experience_group': scoping.experience_groups,
        'target_queue': scoping.queues,
    }


class PublicAgentSerializer(ScopedRelationsMixin, CustomerUserSerializer):
    scoped_relations = {
        'organisation': scoping.customer_organisations,
        'queue': scoping.queues,
    }

    class Meta(CustomerUserSerializer.Meta):
        read_only_fields = [*CustomerUserSerializer.Meta.read_only_fields, 'team']
        extra_kwargs = {'organisation': {'required': True, 'allow_null': False}}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance is not None:
            attrs.pop('email', None)  # the person behind a profile never changes
            return attrs
        email = attrs.get('email')
        if not email:
            raise serializers.ValidationError({'email': 'The email of an existing workspace member is required.'})
        # Unlike the internal endpoint, the public API never creates accounts.
        user = User.objects.filter(
            email__iexact=email,
            organization_memberships__organization_id=self.context['principal'].organization_id,
            organization_memberships__is_active=True,
        ).first()
        if user is None:
            raise serializers.ValidationError({'email': 'No active member of this workspace has that email.'})
        if CustomerUser.objects.filter(user=user, queue=attrs.get('queue')).exists():
            raise serializers.ValidationError({'email': 'This person already has a profile for that queue.'})
        attrs['user'] = user
        attrs.pop('email')
        return attrs

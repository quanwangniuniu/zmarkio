"""
Public API serializers.

The resource serializers subclass the internal CSM ones so the JSON matches
what the product already returns, and swap every writable relation for a
`scoped_*` queryset from public_api.services. The internal serializers check
access through `request.user`; a credential has no user, so those checks are
replaced here rather than skipped.

The admin console serializers (credentials, webhook endpoints, delivery log)
follow at the end.
"""

from django.contrib.auth import get_user_model
from rest_framework import serializers

from chat.services import UnsafeUrlError
from csm.models import CustomerUser
from csm.serializers import (
    ConversationSerializer,
    CustomerUserSerializer,
    QueueSerializer,
    QuickReplyTemplateSerializer,
    RoutingRuleWriteSerializer,
    TicketSerializer,
)
from csm.services.status_machine import get_status
from customer.serializers import CustomerOrganisationSerializer, CustomerSerializer
from public_api import services
from public_api.models import ApiKey, OAuthClient, WebhookDelivery, WebhookEndpoint, WebhookEvent
from public_api.permissions import ALL_SCOPES

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
        'organisation': services.scoped_customer_organisations,
        'sla_policy': services.scoped_sla_policies,
    }

    class Meta(QueueSerializer.Meta):
        read_only_fields = [*QueueSerializer.Meta.read_only_fields, 'project']
        # A queue without a customer organisation can't be attributed to a workspace.
        extra_kwargs = {'organisation': {'required': True, 'allow_null': False}}


class PublicTicketSerializer(ScopedRelationsMixin, TicketSerializer):
    scoped_relations = {
        'queue': services.scoped_queues,
        'conversation': services.scoped_conversations,
        'assigned_to': services.scoped_assignable_users,
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
        'queue': services.scoped_queues,
        'customer': services.scoped_customers,
        'assigned_to': services.scoped_agents,
    }

    class Meta(ConversationSerializer.Meta):
        # Queue-less conversations are outside every credential's scope.
        extra_kwargs = {'queue': {'required': True, 'allow_null': False}}

    def _validate_queue_access(self, queue):
        """Replaced by the scoped `queue` queryset; the base check reads request.user."""


class PublicCustomerSerializer(ScopedRelationsMixin, CustomerSerializer):
    scoped_relations = {
        'experience_group': services.scoped_experience_groups,
        'region': services.scoped_regions,
        'organisation': services.scoped_customer_organisations,
        'status_label': services.scoped_status_labels,
    }


class PublicCustomerOrganisationSerializer(ScopedRelationsMixin, CustomerOrganisationSerializer):
    scoped_relations = {'region': services.scoped_regions}

    class Meta(CustomerOrganisationSerializer.Meta):
        # No nested customer list (unbounded); `organization` is the credential's workspace.
        fields = [f for f in CustomerOrganisationSerializer.Meta.fields if f != 'customers']
        read_only_fields = ['id', 'organization', 'created_at', 'updated_at']

    def validate_name(self, value):
        qs = services.scoped_customer_organisations(self.context['principal']).filter(name__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('An organisation with this name already exists.')
        return value


class PublicTemplateSerializer(ScopedRelationsMixin, QuickReplyTemplateSerializer):
    scoped_relations = {'organisation': services.scoped_customer_organisations}

    class Meta(QuickReplyTemplateSerializer.Meta):
        # Teams are tenant-scoped and not part of the public API.
        read_only_fields = [*QuickReplyTemplateSerializer.Meta.read_only_fields, 'team']

    def validate_organisation(self, value):
        """Replaced by the scoped `organisation` queryset; the base check reads request.user."""
        return value


class PublicRoutingRuleWriteSerializer(ScopedRelationsMixin, RoutingRuleWriteSerializer):
    scoped_relations = {
        'experience_group': services.scoped_experience_groups,
        'target_queue': services.scoped_queues,
    }


class PublicAgentSerializer(ScopedRelationsMixin, CustomerUserSerializer):
    scoped_relations = {
        'organisation': services.scoped_customer_organisations,
        'queue': services.scoped_queues,
    }

    class Meta(CustomerUserSerializer.Meta):
        read_only_fields = [*CustomerUserSerializer.Meta.read_only_fields, 'team']
        extra_kwargs = {
            'organisation': {'required': True, 'allow_null': False},
            # Admins and supervisors usually have no queue; uniqueness is checked in validate().
            'queue': {'required': False, 'allow_null': True},
        }

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


# ---------------------------------------------------------------------------
# Admin console
# ---------------------------------------------------------------------------

def _creator_name(obj):
    user = obj.created_by
    if user is None:
        return None
    return user.get_full_name() or user.email or user.username


class CredentialWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    scopes = serializers.ListField(
        child=serializers.ChoiceField(choices=ALL_SCOPES), allow_empty=False, max_length=len(ALL_SCOPES),
    )

    def validate_scopes(self, value):
        # Stable order regardless of how the client sent them.
        chosen = set(value)
        return [scope for scope in ALL_SCOPES if scope in chosen]


class ApiKeySerializer(serializers.ModelSerializer):
    display_key = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = ApiKey
        fields = [
            'id', 'name', 'display_key', 'scopes', 'is_active',
            'created_by_name', 'created_at', 'revoked_at',
        ]
        read_only_fields = fields

    def get_display_key(self, obj):
        return f'zmk_{obj.prefix}_…'

    def get_created_by_name(self, obj):
        return _creator_name(obj)


class OAuthClientSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = OAuthClient
        fields = ['id', 'name', 'client_id', 'scopes', 'is_active', 'created_by_name', 'created_at', 'revoked_at']
        read_only_fields = fields

    def get_created_by_name(self, obj):
        return _creator_name(obj)


class WebhookEndpointWriteSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=2000)
    events = serializers.ListField(
        child=serializers.ChoiceField(choices=WebhookEvent.choices), allow_empty=False,
        max_length=len(WebhookEvent.choices),
    )

    def validate_url(self, value):
        try:
            services.validate_webhook_url(value)
        except UnsafeUrlError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value

    def validate_events(self, value):
        chosen = set(value)
        return [event for event in WebhookEvent.values if event in chosen]


class WebhookEndpointSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookEndpoint
        fields = ['id', 'url', 'events', 'created_at']
        read_only_fields = fields


class WebhookDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookDelivery
        fields = ['id', 'event_type', 'target_url', 'attempt', 'status', 'response_code', 'error', 'created_at']
        read_only_fields = fields

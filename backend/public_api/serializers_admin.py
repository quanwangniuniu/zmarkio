"""Serializers for the admin console: API credentials, webhook endpoints and the delivery log."""

from rest_framework import serializers

from chat.services import UnsafeUrlError
from public_api.models import ApiKey, OAuthClient, WebhookDelivery, WebhookEndpoint, WebhookEvent
from public_api.scopes import ALL_SCOPES
from public_api.services.webhooks import validate_webhook_url


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
            validate_webhook_url(value)
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

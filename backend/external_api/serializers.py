"""Serializers for the admin console: API credentials, webhook endpoints and the delivery log."""

from rest_framework import serializers

from chat.services import UnsafeUrlError
from external_api import services
from external_api.models import ApiKey, OAuthClient, WebhookDelivery, WebhookEndpoint, WebhookEvent
from external_api.permissions import ALL_SCOPES


class CredentialWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    scopes = serializers.ListField(
        child=serializers.ChoiceField(choices=ALL_SCOPES), allow_empty=False, max_length=len(ALL_SCOPES),
    )


class ApiKeySerializer(serializers.ModelSerializer):
    display_key = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = ApiKey
        fields = ['id', 'name', 'display_key', 'scopes', 'is_active', 'created_at']
        read_only_fields = fields

    def get_display_key(self, obj):
        return f'zmk_{obj.prefix}_…'


class OAuthClientSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = OAuthClient
        fields = ['id', 'name', 'client_id', 'scopes', 'is_active', 'created_at']
        read_only_fields = fields


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


class WebhookEndpointSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookEndpoint
        fields = ['id', 'url', 'events', 'created_at']
        read_only_fields = fields


class WebhookDeliverySerializer(serializers.ModelSerializer):
    target_url = serializers.CharField(source='endpoint.url', read_only=True)

    class Meta:
        model = WebhookDelivery
        fields = ['id', 'event_type', 'target_url', 'attempt', 'status', 'response_code', 'error', 'created_at']
        read_only_fields = fields

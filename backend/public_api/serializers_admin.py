"""Serializers for the admin console: API credentials, webhook endpoints and the delivery log."""

from django.utils import timezone
from rest_framework import serializers

from chat.services import UnsafeUrlError
from public_api.models import ApiKey, OAuthClient, WebhookDelivery, WebhookEndpoint
from public_api.scopes import ALL_SCOPES, EVENT_TYPES, RESOURCES
from public_api.services.http import validate_webhook_url


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

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Name is required.')
        return value

    def validate_scopes(self, value):
        # Stable order regardless of how the client sent them.
        chosen = set(value)
        return [scope for scope in ALL_SCOPES if scope in chosen]


class ApiKeyCreateSerializer(CredentialWriteSerializer):
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate_expires_at(self, value):
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError('Expiry must be in the future.')
        return value


class ApiKeySerializer(serializers.ModelSerializer):
    display_key = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = ApiKey
        fields = [
            'id', 'name', 'prefix', 'display_key', 'scopes', 'is_active',
            'created_by_name', 'created_at', 'last_used_at', 'expires_at', 'revoked_at',
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
        fields = [
            'id', 'name', 'client_id', 'scopes', 'is_active',
            'created_by_name', 'created_at', 'revoked_at',
        ]
        read_only_fields = fields

    def get_created_by_name(self, obj):
        return _creator_name(obj)


class WebhookEndpointWriteSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=2000)
    description = serializers.CharField(max_length=200, required=False, allow_blank=True)
    events = serializers.ListField(
        child=serializers.ChoiceField(choices=EVENT_TYPES), allow_empty=False, max_length=len(EVENT_TYPES),
    )
    is_active = serializers.BooleanField(required=False)

    def validate_url(self, value):
        try:
            validate_webhook_url(value)
        except UnsafeUrlError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value

    def validate_events(self, value):
        chosen = set(value)
        return [event for event in EVENT_TYPES if event in chosen]


class WebhookEndpointSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    # Annotated by the viewset from the newest delivery row.
    last_delivery_status = serializers.CharField(read_only=True, default=None)
    last_delivery_at = serializers.DateTimeField(read_only=True, default=None)

    class Meta:
        model = WebhookEndpoint
        fields = [
            'id', 'url', 'description', 'events', 'is_active',
            'created_by_name', 'created_at', 'updated_at',
            'last_delivery_status', 'last_delivery_at',
        ]
        read_only_fields = fields

    def get_created_by_name(self, obj):
        return _creator_name(obj)


class WebhookDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookDelivery
        fields = [
            'id', 'endpoint', 'event_id', 'event_type', 'target_url', 'attempt', 'status',
            'response_code', 'error', 'duration_ms', 'next_retry_at', 'created_at', 'payload',
        ]
        read_only_fields = fields


def vocabulary_payload():
    return {
        'resources': list(RESOURCES),
        'scopes': list(ALL_SCOPES),
        'events': list(EVENT_TYPES),
    }

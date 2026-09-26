from functools import cached_property

from rest_framework import serializers
from core.tenant_context import current_tenant_schema

from .access import (
    user_can_manage_meta_ad_account,
    user_can_sync_meta_ad_account,
)
from .models import FacebookConnection, MetaAdAccount


class MetaAdAccountSerializer(serializers.ModelSerializer):
    project_id = serializers.SerializerMethodField()
    connected_by_current_user = serializers.SerializerMethodField()
    can_manage = serializers.SerializerMethodField()
    can_sync = serializers.SerializerMethodField()
    connector_name = serializers.SerializerMethodField()

    class Meta:
        model = MetaAdAccount
        fields = [
            "id",
            "meta_account_id",
            "name",
            "currency",
            "timezone_name",
            "account_status",
            "business_id",
            "is_owned",
            "project_id",
            "connected_by_current_user",
            "can_manage",
            "can_sync",
            "connector_name",
        ]

    def _request_user(self):
        request = self.context.get("request")
        return getattr(request, "user", None) if request else None

    @cached_property
    def _project_schema(self):
        return current_tenant_schema()

    def get_project_id(self, obj):
        return obj.project_id if obj.project_schema == self._project_schema else None

    def get_connected_by_current_user(self, obj):
        user = self._request_user()
        return bool(user and obj.connection.user_id == user.id)

    def get_can_manage(self, obj):
        user = self._request_user()
        return user_can_manage_meta_ad_account(user, obj)

    def get_can_sync(self, obj):
        user = self._request_user()
        return user_can_sync_meta_ad_account(user, obj)

    def get_connector_name(self, obj):
        user = obj.connection.user
        return user.get_full_name().strip() or user.username or user.email


class FacebookConnectionStatusSerializer(serializers.Serializer):
    connected = serializers.BooleanField()
    fb_user_name = serializers.CharField(required=False, allow_blank=True)
    fb_email = serializers.EmailField(required=False, allow_null=True)
    business_id = serializers.CharField(required=False, allow_blank=True)
    business_name = serializers.CharField(required=False, allow_blank=True)
    token_expires_at = serializers.DateTimeField(required=False, allow_null=True)
    last_synced_at = serializers.DateTimeField(required=False, allow_null=True)
    ad_accounts = MetaAdAccountSerializer(many=True, required=False)


class ConnectInitSerializer(serializers.Serializer):
    authorize_url = serializers.URLField()
    state = serializers.CharField()


class LinkProjectSerializer(serializers.Serializer):
    project_id = serializers.IntegerField(allow_null=True)

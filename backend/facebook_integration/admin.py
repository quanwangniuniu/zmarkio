from django.contrib import admin

from .models import FacebookConnection, MetaAdAccount


@admin.register(FacebookConnection)
class FacebookConnectionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "fb_user_name",
        "business_name",
        "is_active",
        "token_expires_at",
        "last_synced_at",
        "updated_at",
    )
    list_filter = ("is_active",)
    search_fields = ("user__email", "fb_user_name", "fb_user_id", "business_id")
    readonly_fields = ("encrypted_access_token", "created_at", "updated_at")


@admin.register(MetaAdAccount)
class MetaAdAccountAdmin(admin.ModelAdmin):
    list_display = (
        "meta_account_id",
        "name",
        "currency",
        "account_status",
        "is_owned",
        "connection",
        "project_link",
        "updated_at",
    )
    list_filter = ("is_owned", "currency", "account_status")
    search_fields = ("meta_account_id", "name", "business_id")
    # A bare project FK cannot represent a tenant-local project safely. Relink
    # through the integration endpoint, which saves schema and ID together.
    exclude = ("project",)
    readonly_fields = ("project_link",)

    @admin.display(description="Project (schema / ID)")
    def project_link(self, obj):
        if obj.project_id is None:
            return "—"
        return f"{obj.project_schema} / {obj.project_id}"

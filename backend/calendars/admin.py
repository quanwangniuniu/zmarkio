from django.contrib import admin

from calendars.models import BookingLink


@admin.register(BookingLink)
class BookingLinkAdmin(admin.ModelAdmin):
    """
    Admin for booking links.

    Note these are tenant-scoped rows: the admin runs on whatever schema the
    request resolves to, so a link only appears here for staff whose own
    organisation owns it.
    """

    list_display = [
        "slug",
        "title",
        "owner",
        "organization",
        "duration_minutes",
        "is_active",
        "invitees_only",
        "updated_at",
    ]
    list_filter = ["is_active", "invitees_only", "organization"]
    search_fields = ["slug", "title", "owner__email", "owner__username"]
    # raw_id rather than autocomplete: autocomplete_fields requires the related
    # models to have their own registered admin with search_fields, and Calendar
    # has none.
    raw_id_fields = ["owner", "calendar", "organization"]
    readonly_fields = ["id", "created_at", "updated_at"]
    fieldsets = (
        (None, {"fields": ("id", "organization", "owner", "calendar", "is_active", "invitees_only")}),
        ("Link", {"fields": ("slug", "title", "description")}),
        (
            "Scheduling rules",
            {
                "fields": (
                    "duration_minutes",
                    "slot_increment_minutes",
                    "buffer_before_minutes",
                    "buffer_after_minutes",
                    "min_notice_minutes",
                    "max_advance_days",
                    "timezone",
                    "availability_windows",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

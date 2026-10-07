"""
Key updates vs previous version:
- Explicit multi-tenancy: every model is scoped by organization (FK -> core.Organization)
- Tenant safety: cross-organization references are validated in clean()
- Removed invalid DB CheckConstraint that attempted cross-table join (calendar__owner) and replaced with clean()
- Bulk-friendly validation: save(validate=True) with opt-out for imports/sync jobs
- RecurrenceException recursion guard: modified_event cannot be recurring / cannot have recurrence rule
- Fixed attendee email autofill by allowing blank email when user provided
- Fixed reminder timing fields so time_value/time_unit can be used without requiring minutes_before upfront
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel, Organization, Project

User = get_user_model()


# -----------------------------
# Helpers
# -----------------------------
def _user_org_id(user: User | None) -> uuid.UUID | int | None:
    if not user:
        return None
    return getattr(user, "organization_id", None)


def _ensure_same_org(model_name: str, left_label: str, left_org_id, right_label: str, right_org_id):
    if left_org_id and right_org_id and left_org_id != right_org_id:
        raise ValidationError(
            { "organization": f"{model_name}: {left_label}.organization != {right_label}.organization" }
        )


# -----------------------------
# Calendar
# -----------------------------
class Calendar(TimeStampedModel):
    """
    Core Calendar model - represents a calendar owned by a user.
    Users can own multiple calendars and share them with different permission levels.
    """

    VISIBILITY_CHOICES = [
        ("private", "Private - Only owner can see"),
        ("shared", "Shared - Visible to specific users"),
        ("public", "Public - Anyone with link can view"),
    ]

    COLOR_CHOICES = [
        ("#1E88E5", "Blue"),
        ("#E53935", "Red"),
        ("#43A047", "Green"),
        ("#FB8C00", "Orange"),
        ("#8E24AA", "Purple"),
        ("#3949AB", "Indigo"),
        ("#00ACC1", "Cyan"),
        ("#F4511E", "Deep Orange"),
        ("#6D4C41", "Brown"),
        ("#546E7A", "Blue Grey"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Multi-tenancy boundary
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="calendars",
        help_text="Tenant boundary. Must match owner's organization.",
    )

    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="owned_calendars",
        help_text="Calendar owner",
        null=True,
        blank=True,
    )

    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        related_name="calendars",
        null=True,
        blank=True,
        help_text="Optional project binding for project-scoped calendars.",
    )

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    color = models.CharField(max_length=7, choices=COLOR_CHOICES, default="#1E88E5")
    visibility = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default="private")
    timezone = models.CharField(max_length=100, default="UTC")

    is_primary = models.BooleanField(default=False)

    location = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        ordering = ["-is_primary", "-created_at"]
        indexes = [
            models.Index(fields=["organization", "owner", "is_deleted"]),
            models.Index(fields=["organization", "owner", "is_primary"]),
            models.Index(fields=["organization", "visibility"]),
            models.Index(fields=["organization", "project", "is_deleted"], name="calendars_c_organiz_87e5ba_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "owner", "name"],
                condition=models.Q(is_deleted=False) & models.Q(owner__isnull=False),
                name="unique_calendar_name_per_owner_per_org",
            ),
            models.UniqueConstraint(
                fields=["organization", "owner"],
                condition=models.Q(is_primary=True) & models.Q(is_deleted=False) & models.Q(owner__isnull=False),
                name="unique_primary_calendar_per_owner_per_org",
            ),
            models.UniqueConstraint(
                fields=["project"],
                condition=models.Q(is_deleted=False) & models.Q(project__isnull=False),
                name="unique_project_calendar",
            ),
        ]

    def __str__(self):
        owner_label = getattr(self.owner, "email", self.owner_id) or "no-owner"
        return f"{owner_label} - {self.name}"

    def clean(self):
        super().clean()

        # Personal calendars: owner must belong to the same organization.
        # Project-bound calendars use project.organization; the owner may be a
        # cross-org project member while still matching project.owner.
        if not self.project_id:
            owner_org_id = _user_org_id(self.owner)
            if owner_org_id and self.organization_id and owner_org_id != self.organization_id:
                raise ValidationError({"organization": "Calendar.organization must match owner.organization"})

        if self.project_id:
            if self.project.organization_id != self.organization_id:
                raise ValidationError({"project": "Calendar.project must match Calendar.organization"})
            if self.project.owner_id and self.owner_id != self.project.owner_id:
                raise ValidationError({"owner": "Calendar.owner must match project.owner"})


    def save(self, *args, validate: bool = True, **kwargs):
        # Enforce one primary calendar per owner per org (excluding soft-deleted)
        # This must happen BEFORE full_clean() to avoid violating the unique constraint
        if self.is_primary and self.owner_id:
            Calendar.objects.filter(
                organization_id=self.organization_id,
                owner_id=self.owner_id,
                is_primary=True,
                is_deleted=False,
            ).exclude(pk=self.pk).update(is_primary=False)

        if validate:
            self.full_clean()

        super().save(*args, **kwargs)

# -----------------------------
# Calendar Subscription
# -----------------------------
class CalendarSubscription(TimeStampedModel):
    """
    Per-user subscription to a calendar.
    Separates 'Viewing Preferences' from 'Calendar Data'.
    Allows User A to see User B's calendar in Red, while User B sees it in Blue.
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="calendar_subscriptions",
        help_text="Tenant boundary.",
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="calendar_subscriptions",
        help_text="The subscriber (who is viewing)",
    )

    # The internal calendar being viewed
    calendar = models.ForeignKey(
        Calendar, 
        on_delete=models.CASCADE, 
        related_name="subscriptions",
        null=True, blank=True
    )

    # For external iCal feeds (e.g., Google Holidays)
    source_url = models.URLField(blank=True, null=True, help_text="External iCal URL")

    # Personalization
    color_override = models.CharField(max_length=7, blank=True, null=True)
    is_hidden = models.BooleanField(default=False, help_text="Hide from view without unsubscribing")
    notification_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "user", "is_hidden"]),
            models.Index(fields=["organization", "calendar"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user", "calendar"],
                condition=models.Q(is_deleted=False) & models.Q(calendar__isnull=False),
                name="unique_internal_subscription",
            ),
            models.UniqueConstraint(
                fields=["organization", "user", "source_url"],
                condition=models.Q(is_deleted=False) & models.Q(source_url__isnull=False),
                name="unique_external_subscription",
            ),
            models.CheckConstraint(
                check=(
                    (models.Q(calendar__isnull=False) & models.Q(source_url__isnull=True))
                    | (models.Q(calendar__isnull=True) & models.Q(source_url__isnull=False))
                ),
                name="subscription_calendar_or_source_url",
            ),
        ]

    def __str__(self):
        target = self.calendar.name if self.calendar else self.source_url
        return f"{getattr(self.user, 'email', self.user_id)} -> {target}"

    def clean(self):
        super().clean()

        # Organization consistency
        user_org_id = _user_org_id(self.user)
        if user_org_id and self.organization_id and user_org_id != self.organization_id:
             raise ValidationError({"organization": "Subscription.organization must match user.organization"})
        
        if self.calendar_id:
            if self.calendar.organization_id != self.organization_id:
                raise ValidationError({"calendar": "Cannot subscribe to calendar from different organization"})

        # Must have either calendar OR source_url
        if not self.calendar_id and not self.source_url:
            raise ValidationError("Subscription must target either a 'calendar' or 'source_url'")

    def save(self, *args, validate: bool = True, **kwargs):
        if validate:
            self.full_clean()
        super().save(*args, **kwargs)

# -----------------------------
# Calendar Share (ACL)
# -----------------------------
class CalendarShare(TimeStampedModel):
    """
    Calendar sharing model - manages calendar access permissions.
    """

    PERMISSION_CHOICES = [
        ("view_free_busy", "See only free/busy (hide details)"),
        ("view_all", "View all event details"),
        ("edit", "Make changes to events"),
        ("manage", "Make changes and manage sharing"),
        ("owner", "Owner - full control"),
    ]

    PERMISSION_LEVELS = {
        "view_free_busy": 10,
        "view_all": 20,
        "edit": 30,
        "manage": 40,
        "owner": 50,
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="calendar_shares",
        help_text="Tenant boundary. Must match calendar.organization and shared_with.organization.",
    )

    calendar = models.ForeignKey(Calendar, on_delete=models.CASCADE, related_name="shares")
    shared_with = models.ForeignKey(User, on_delete=models.CASCADE, related_name="shared_calendars")

    permission = models.CharField(max_length=20, choices=PERMISSION_CHOICES, default="view_all")

    can_invite_others = models.BooleanField(default=False)
    notification_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "calendar", "shared_with"]),
            models.Index(fields=["organization", "shared_with", "is_deleted"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "calendar", "shared_with"],
                condition=models.Q(is_deleted=False),
                name="unique_calendar_share_per_user_per_org",
            ),
        ]

    def __str__(self):
        return f"{self.calendar.name} shared with {getattr(self.shared_with, 'email', self.shared_with_id)} ({self.permission})"

    def clean(self):
        super().clean()

        # org must match calendar.org
        if self.calendar_id and self.organization_id and self.calendar.organization_id != self.organization_id:
            raise ValidationError({"organization": "CalendarShare.organization must match calendar.organization"})

        # org must match shared_with.org
        shared_with_org_id = _user_org_id(self.shared_with)
        _ensure_same_org("CalendarShare", "calendar", self.calendar.organization_id, "shared_with", shared_with_org_id)

        # prevent owner in shares (DB join checks aren't allowed in CheckConstraint reliably)
        if self.calendar_id and self.shared_with_id and self.calendar.owner_id == self.shared_with_id:
            raise ValidationError({"shared_with": "Owner should not appear in CalendarShare (owner already has full access)."})

    @property
    def permission_level(self) -> int:
        return int(self.PERMISSION_LEVELS.get(self.permission, 0))

    def has_permission(self, required_permission: str) -> bool:
        required_level = int(self.PERMISSION_LEVELS.get(required_permission, 0))
        return self.permission_level >= required_level

    def save(self, *args, validate: bool = True, **kwargs):
        if validate:
            self.full_clean()
        super().save(*args, **kwargs)


# -----------------------------
# Recurrence Rule (RFC 5545 RRULE)
# -----------------------------
class RecurrenceRule(TimeStampedModel):
    """
    Recurrence pattern model based on RFC 5545 RRULE.
    Stored once; instances are generated in application logic.
    """

    FREQUENCY_CHOICES = [("DAILY", "Daily"), ("WEEKLY", "Weekly"), ("MONTHLY", "Monthly"), ("YEARLY", "Yearly")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="recurrence_rules",
        help_text="Tenant boundary.",
    )

    frequency = models.CharField(max_length=10, choices=FREQUENCY_CHOICES)
    interval = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(1000)])

    by_day = models.JSONField(default=list, blank=True)
    by_month_day = models.JSONField(default=list, blank=True)
    by_set_pos = models.JSONField(default=list, blank=True)
    by_month = models.JSONField(default=list, blank=True)

    count = models.PositiveIntegerField(null=True, blank=True, validators=[MaxValueValidator(999)])
    until = models.DateTimeField(null=True, blank=True)

    exception_dates = models.JSONField(default=list, blank=True)
    rrule_string = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "frequency"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(count__isnull=True, until__isnull=False)
                    | models.Q(count__isnull=False, until__isnull=True)
                    | models.Q(count__isnull=True, until__isnull=True)
                ),
                name="recurrence_count_or_until",
            )
        ]

    def __str__(self):
        pattern = f"{self.get_frequency_display()}"
        if self.interval > 1:
            pattern += f" (every {self.interval})"
        if self.count:
            pattern += f" - {self.count} times"
        elif self.until:
            pattern += f" - until {self.until.strftime('%Y-%m-%d')}"
        return pattern

    def generate_rrule_string(self) -> str:
        parts = [f"FREQ={self.frequency}"]

        if self.interval and self.interval > 1:
            parts.append(f"INTERVAL={self.interval}")

        if self.by_day:
            parts.append(f"BYDAY={','.join(self.by_day)}")
        if self.by_month_day:
            parts.append(f"BYMONTHDAY={','.join(map(str, self.by_month_day))}")
        if self.by_month:
            parts.append(f"BYMONTH={','.join(map(str, self.by_month))}")
        if self.by_set_pos:
            parts.append(f"BYSETPOS={','.join(map(str, self.by_set_pos))}")

        if self.count:
            parts.append(f"COUNT={self.count}")
        elif self.until:
            parts.append(f"UNTIL={self.until.strftime('%Y%m%dT%H%M%SZ')}")

        return ";".join(parts)

    def save(self, *args, validate: bool = True, **kwargs):
        if validate:
            self.full_clean()
        super().save(*args, **kwargs)


# -----------------------------
# Event
# -----------------------------
class EventQuerySet(models.QuerySet):
    """
    Custom queryset for Event to support common calendar queries.
    """

    def active(self):
        return self.filter(is_deleted=False)

    def for_organization(self, organization):
        return self.filter(organization=organization)

    def for_calendars(self, calendars):
        return self.filter(calendar__in=calendars)

    def in_timerange(self, start, end):
        """
        Events that intersect [start, end).
        """
        return self.filter(start_datetime__lt=end, end_datetime__gt=start)


class Event(TimeStampedModel):
    """
    Core Event model - represents calendar events.
    Supports single events, all-day events, multi-day events.
    """

    STATUS_CHOICES = [("confirmed", "Confirmed"), ("tentative", "Tentative"), ("cancelled", "Cancelled")]
    EVENT_TYPE_CHOICES = [
        ("default", "Default Event"),
        ("out_of_office", "Out of Office"),
        ("focus_time", "Focus Time"),
        ("working_location", "Working Location"),
    ]

    VISIBILITY_CHOICES = [("default", "Default"), ("public", "Public"), ("private", "Private")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="events",
        help_text="Tenant boundary. Must match calendar.organization.",
    )

    calendar = models.ForeignKey(Calendar, on_delete=models.CASCADE, related_name="events")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="created_events")

    title = models.CharField(max_length=500)
    description = models.TextField(blank=True, null=True)

    start_datetime = models.DateTimeField(help_text="Event start (UTC recommended)")
    end_datetime = models.DateTimeField(help_text="Event end (UTC recommended)")
    timezone = models.CharField(max_length=100, default="UTC")
    is_all_day = models.BooleanField(default=False)

    location = models.CharField(max_length=500, blank=True, null=True)
    location_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    location_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="confirmed")
    event_type = models.CharField(max_length=30, choices=EVENT_TYPE_CHOICES, default="default")
    color = models.CharField(max_length=7, blank=True, null=True)

    visibility = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default="default")

    # Recurring
    is_recurring = models.BooleanField(default=False)
    recurrence_rule = models.ForeignKey(
        RecurrenceRule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        help_text="Recurrence pattern for this series (if recurring).",
    )
    original_start = models.DateTimeField(null=True, blank=True, help_text="Original start for a modified instance")

    has_conference = models.BooleanField(default=False)
    conference_data = models.JSONField(default=dict, blank=True)

    guests_can_modify = models.BooleanField(default=False)
    guests_can_invite_others = models.BooleanField(default=True)
    guests_can_see_other_guests = models.BooleanField(default=True)

    attachments = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    external_id = models.CharField(max_length=255, blank=True, null=True)
    ical_uid = models.CharField(max_length=255, blank=True, null=True)
    etag = models.CharField(max_length=64, blank=True, null=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        ordering = ["start_datetime"]
        indexes = [
            models.Index(fields=["organization", "calendar", "start_datetime", "end_datetime"]),
            models.Index(fields=["organization", "calendar", "is_deleted"]),
            models.Index(fields=["organization", "start_datetime", "end_datetime"]),
            models.Index(fields=["organization", "created_by", "is_deleted"]),
            models.Index(fields=["organization", "is_recurring", "recurrence_rule"]),
            models.Index(fields=["organization", "status", "start_datetime"]),
            models.Index(fields=["organization", "calendar", "status", "start_datetime"]),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(end_datetime__gt=models.F("start_datetime")), name="event_end_after_start"),
            models.UniqueConstraint(
                fields=["organization", "ical_uid"],
                condition=models.Q(is_deleted=False) & models.Q(ical_uid__isnull=False),
                name="unique_ical_uid_per_org",
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.start_datetime.strftime('%Y-%m-%d %H:%M')})"

    @property
    def duration_minutes(self) -> int:
        if self.start_datetime and self.end_datetime:
            delta = self.end_datetime - self.start_datetime
            return int(delta.total_seconds() / 60)
        return 0

    @property
    def is_multi_day(self) -> bool:
        return self.start_datetime.date() != self.end_datetime.date()

    def clean(self):
        super().clean()

        # organization must match calendar.organization
        if self.calendar_id and self.organization_id and self.calendar.organization_id != self.organization_id:
            raise ValidationError({"organization": "Event.organization must match calendar.organization"})

        # created_by org must match (if set)
        if self.created_by_id:
            creator_org_id = _user_org_id(self.created_by)
            calendar_project_id = self.calendar.project_id if self.calendar_id else None
            if (
                creator_org_id
                and self.organization_id
                and creator_org_id != self.organization_id
                and not calendar_project_id
            ):
                raise ValidationError({"created_by": "created_by.organization must match Event.organization"})

        # Recurrence integrity
        if self.is_recurring:
            if not self.recurrence_rule_id:
                raise ValidationError({"recurrence_rule": "Recurring event must have recurrence_rule"})
            if self.recurrence_rule and self.recurrence_rule.organization_id != self.organization_id:
                raise ValidationError({"recurrence_rule": "recurrence_rule.organization must match Event.organization"})
        else:
            # Non-recurring events should not point to a recurrence rule (avoid ambiguity)
            if self.recurrence_rule_id:
                raise ValidationError({"recurrence_rule": "Non-recurring event should not set recurrence_rule"})

        # If this is a modified instance, require original_start for traceability (recommended)
        if self.original_start and not self.is_recurring:
            # allowed; used for exceptions
            pass

    def save(self, *args, validate: bool = True, **kwargs):
        if not self.ical_uid:
            self.ical_uid = f"{self.id}@mediajira-calendar"

        if validate:
            self.full_clean()

        super().save(*args, **kwargs)

        # Generate/update ETag based on updated_at and id
        from hashlib import sha1  # Local import to avoid circulars

        if self.updated_at and self.id:
            new_etag = sha1(f"{self.updated_at.isoformat()}-{self.id}".encode("utf-8")).hexdigest()
            if new_etag != self.etag:
                type(self).objects.filter(pk=self.pk).update(etag=new_etag)
                self.etag = new_etag


# -----------------------------
# Recurrence Exception
# -----------------------------
class RecurrenceException(TimeStampedModel):
    """
    Modified or deleted instances of recurring events.
    Stores exception per occurrence without duplicating all occurrences.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="recurrence_exceptions",
        help_text="Tenant boundary. Must match recurrence_rule / events org.",
    )

    recurrence_rule = models.ForeignKey(RecurrenceRule, on_delete=models.CASCADE, related_name="exceptions")
    original_event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="recurrence_exceptions")

    exception_date = models.DateTimeField(help_text="Occurrence start datetime being modified/deleted (UTC recommended)")
    is_cancelled = models.BooleanField(default=False, help_text="True if this specific instance is cancelled")

    modified_event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="exception_modifications",
        help_text="Full modified event (optional). Null when deleted.",
    )

    class Meta:
        ordering = ["exception_date"]
        indexes = [
            models.Index(fields=["organization", "recurrence_rule", "exception_date"]),
            models.Index(fields=["organization", "original_event", "exception_date"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "recurrence_rule", "exception_date"],
                name="unique_exception_per_date_per_org",
            )
        ]

    def __str__(self):
        action = "Cancelled" if self.is_cancelled else "Modified"
        return f"{action} instance: {self.original_event.title} on {self.exception_date.strftime('%Y-%m-%d')}"

    def clean(self):
        super().clean()

        # Tenant consistency
        if self.recurrence_rule_id and self.organization_id and self.recurrence_rule.organization_id != self.organization_id:
            raise ValidationError({"organization": "RecurrenceException.organization must match recurrence_rule.organization"})
        if self.original_event_id and self.organization_id and self.original_event.organization_id != self.organization_id:
            raise ValidationError({"organization": "RecurrenceException.organization must match original_event.organization"})

        # original_event must belong to recurrence_rule (recommended sanity)
        if self.original_event_id and self.recurrence_rule_id:
            if self.original_event.recurrence_rule_id != self.recurrence_rule_id:
                raise ValidationError({"original_event": "original_event.recurrence_rule must equal recurrence_rule"})

        # Behavior constraints
        if self.is_cancelled:
            if self.modified_event_id:
                raise ValidationError({"modified_event": "Cancelled exception must NOT have modified_event"})
        else:
            if not self.modified_event_id:
                raise ValidationError({"modified_event": "Modified exception must have modified_event"})
            # Recursion guard: modified_event must NOT be recurring and must NOT have recurrence_rule
            if self.modified_event.is_recurring:
                raise ValidationError({"modified_event": "modified_event must not be recurring"})
            if self.modified_event.recurrence_rule_id:
                raise ValidationError({"modified_event": "modified_event must not have recurrence_rule"})
            # org/calendar should align (practical expectation)
            if self.modified_event.organization_id != self.organization_id:
                raise ValidationError({"modified_event": "modified_event.organization must match RecurrenceException.organization"})
            if self.modified_event.calendar_id != self.original_event.calendar_id:
                raise ValidationError({"modified_event": "modified_event.calendar should match original_event.calendar"})
            # optional: ensure original_start is set (helps rendering/debug)
            if not self.modified_event.original_start:
                # not strictly required, but recommended
                pass

    def save(self, *args, validate: bool = True, **kwargs):
        if validate:
            self.full_clean()
        super().save(*args, **kwargs)


# -----------------------------
# Attendees
# -----------------------------
class EventAttendee(TimeStampedModel):
    """
    Event attendees and their RSVP status.
    Supports both internal users and external email addresses.
    """

    RESPONSE_STATUS_CHOICES = [
        ("needs_action", "No response yet"),
        ("accepted", "Accepted"),
        ("declined", "Declined"),
        ("tentative", "Tentative/Maybe"),
    ]

    ATTENDEE_TYPE_CHOICES = [
        ("required", "Required"),
        ("optional", "Optional"),
        ("resource", "Resource (room, equipment)"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="event_attendees",
        help_text="Tenant boundary. Must match event.organization.",
    )

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="attendees")

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="event_attendances",
        help_text="Internal user (if registered).",
    )

    # allow blank to support user autofill
    email = models.EmailField(blank=True, help_text="Attendee email address")
    display_name = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text=(
            "Optional contact number. Collected from guests booking through a "
            "public link, who have no profile to look one up from."
        ),
    )

    attendee_type = models.CharField(max_length=20, choices=ATTENDEE_TYPE_CHOICES, default="required")
    response_status = models.CharField(max_length=20, choices=RESPONSE_STATUS_CHOICES, default="needs_action")

    response_comment = models.TextField(blank=True, null=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    is_organizer = models.BooleanField(default=False)
    can_modify = models.BooleanField(default=False)

    notification_enabled = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-is_organizer", "attendee_type", "email"]
        indexes = [
            models.Index(fields=["organization", "event", "user"]),
            models.Index(fields=["organization", "event", "email"]),
            models.Index(fields=["organization", "user", "response_status"]),
            models.Index(fields=["organization", "email", "response_status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "event", "email"],
                condition=models.Q(is_deleted=False),
                name="unique_attendee_per_event_per_org",
            )
        ]

    def __str__(self):
        name = self.display_name or self.email or str(self.user_id)
        return f"{name} - {self.event.title} ({self.get_response_status_display()})"

    def clean(self):
        super().clean()

        if self.event_id and self.organization_id and self.event.organization_id != self.organization_id:
            raise ValidationError({"organization": "EventAttendee.organization must match event.organization"})

        if self.user_id:
            user_org_id = _user_org_id(self.user)
            event_project_id = self.event.calendar.project_id if self.event_id else None
            if (
                user_org_id
                and self.organization_id
                and user_org_id != self.organization_id
                and not event_project_id
            ):
                raise ValidationError({"user": "Attendee user.organization must match EventAttendee.organization"})

        # Email required either directly or via user
        if not self.email and not self.user_id:
            raise ValidationError({"email": "Attendee requires email or user"})

    def save(self, *args, validate: bool = True, **kwargs):
        # Sync email/display_name if user is provided
        if self.user_id:
            if not self.email:
                self.email = self.user.email
            if not self.display_name:
                full_name = getattr(self.user, "get_full_name", lambda: "")() or ""
                self.display_name = full_name.strip() or self.user.email

        # Update responded_at when response changes away from needs_action
        if self.pk:
            try:
                old = EventAttendee.objects.get(pk=self.pk)
                if old.response_status != self.response_status and self.response_status != "needs_action":
                    self.responded_at = timezone.now()
            except EventAttendee.DoesNotExist:
                pass

        if validate:
            self.full_clean()
        super().save(*args, **kwargs)


# -----------------------------
# Reminders
# -----------------------------
class EventReminder(TimeStampedModel):
    """
    Event reminders and notifications.
    Supports multiple reminders per event with different methods and timings.
    """

    REMINDER_METHOD_CHOICES = [
        ("email", "Email"),
        ("notification", "In-app notification"),
        ("popup", "Pop-up alert"),
        ("sms", "SMS"),
    ]

    TIME_UNIT_CHOICES = [
        ("minutes", "Minutes"),
        ("hours", "Hours"),
        ("days", "Days"),
        ("weeks", "Weeks"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="event_reminders",
        help_text="Tenant boundary. Must match event.organization.",
    )

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="reminders")
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name="event_reminders")

    method = models.CharField(max_length=20, choices=REMINDER_METHOD_CHOICES, default="notification")

    # Allow either minutes_before OR (time_value,time_unit)
    minutes_before = models.PositiveIntegerField(null=True, blank=True)
    time_value = models.PositiveIntegerField(null=True, blank=True)
    time_unit = models.CharField(max_length=10, choices=TIME_UNIT_CHOICES, null=True, blank=True)

    is_sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    scheduled_time = models.DateTimeField(null=True, blank=True)

    send_attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["event", "minutes_before"]
        indexes = [
            models.Index(fields=["organization", "event", "user"]),
            models.Index(fields=["organization", "scheduled_time", "is_sent"]),
            models.Index(fields=["organization", "is_sent", "scheduled_time"]),
        ]

    def __str__(self):
        mb = self.minutes_before if self.minutes_before is not None else "?"
        return f"Reminder: {self.event.title} - {mb}min before ({self.method})"

    def clean(self):
        super().clean()

        if self.event_id and self.organization_id and self.event.organization_id != self.organization_id:
            raise ValidationError({"organization": "EventReminder.organization must match event.organization"})

        if self.user_id:
            user_org_id = _user_org_id(self.user)
            event_project_id = self.event.calendar.project_id if self.event_id else None
            if (
                user_org_id
                and self.organization_id
                and user_org_id != self.organization_id
                and not event_project_id
            ):
                raise ValidationError({"user": "Reminder user.organization must match EventReminder.organization"})

        # Must be able to compute minutes_before
        if self.minutes_before is None:
            if self.time_value is None or not self.time_unit:
                raise ValidationError(
                    {"minutes_before": "Provide minutes_before or (time_value + time_unit) to define reminder timing"}
                )

    def save(self, *args, validate: bool = True, **kwargs):
        # Convert time_value/time_unit -> minutes_before if needed
        if self.minutes_before is None and self.time_value is not None and self.time_unit:
            conversions = {"minutes": 1, "hours": 60, "days": 1440, "weeks": 10080}
            self.minutes_before = int(self.time_value) * int(conversions.get(self.time_unit, 1))

        # Calculate scheduled_time
        if self.event_id and self.minutes_before is not None:
            self.scheduled_time = self.event.start_datetime - timedelta(minutes=int(self.minutes_before))

        if validate:
            self.full_clean()
        super().save(*args, **kwargs)


# -----------------------------
# Categories
# -----------------------------
class EventCategory(TimeStampedModel):
    """
    Event categories/labels for organization.
    Users can create custom categories and assign colors.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="event_categories",
        help_text="Tenant boundary. Must match user.organization.",
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="event_categories")

    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, default="#1E88E5")
    description = models.TextField(blank=True, null=True)
    is_system = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "Event categories"
        ordering = ["user", "name"]
        indexes = [
            models.Index(fields=["organization", "user", "is_deleted"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user", "name"],
                condition=models.Q(is_deleted=False),
                name="unique_category_per_user_per_org",
            )
        ]

    def __str__(self):
        return f"{getattr(self.user, 'email', self.user_id)} - {self.name}"

    def clean(self):
        super().clean()

        user_org_id = _user_org_id(self.user)
        if user_org_id and self.organization_id and user_org_id != self.organization_id:
            raise ValidationError({"organization": "EventCategory.organization must match user.organization"})

    def save(self, *args, validate: bool = True, **kwargs):
        if validate:
            self.full_clean()
        super().save(*args, **kwargs)


class EventCategoryAssignment(TimeStampedModel):
    """
    Many-to-many relationship between events and categories.
    Events can have multiple categories.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="event_category_assignments",
        help_text="Tenant boundary. Must match event/category org.",
    )

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="category_assignments")
    category = models.ForeignKey(EventCategory, on_delete=models.CASCADE, related_name="event_assignments")

    class Meta:
        ordering = ["event", "category"]
        indexes = [
            models.Index(fields=["organization", "event", "category"]),
            models.Index(fields=["organization", "category", "is_deleted"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "event", "category"],
                condition=models.Q(is_deleted=False),
                name="unique_category_assignment_per_org",
            )
        ]

    def __str__(self):
        return f"{self.event.title} -> {self.category.name}"

    def clean(self):
        super().clean()

        if self.organization_id:
            if self.event_id and self.event.organization_id != self.organization_id:
                raise ValidationError({"organization": "Assignment.organization must match event.organization"})
            if self.category_id and self.category.organization_id != self.organization_id:
                raise ValidationError({"organization": "Assignment.organization must match category.organization"})
        if self.event_id and self.category_id:
            if self.event.organization_id != self.category.organization_id:
                raise ValidationError({"category": "Event and Category must belong to the same organization"})

    def save(self, *args, validate: bool = True, **kwargs):
        if validate:
            self.full_clean()
        super().save(*args, **kwargs)


# -----------------------------
# Calendar Settings
# -----------------------------
class CalendarSettings(TimeStampedModel):
    """
    User-specific calendar settings and preferences.
    Stores defaults for creating events, notification preferences, etc.
    """

    WEEK_START_CHOICES = [(0, "Sunday"), (1, "Monday"), (6, "Saturday")]
    TIME_FORMAT_CHOICES = [("12h", "12-hour (1:00 PM)"), ("24h", "24-hour (13:00)")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="calendar_settings",
        help_text="Tenant boundary. Must match user.organization.",
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="calendar_settings")

    default_view = models.CharField(
        max_length=20,
        choices=[("day", "Day view"), ("week", "Week view"), ("month", "Month view"), ("year", "Year view"), ("agenda", "Agenda view")],
        default="week",
    )
    week_start = models.IntegerField(choices=WEEK_START_CHOICES, default=1)
    time_format = models.CharField(max_length=5, choices=TIME_FORMAT_CHOICES, default="12h")
    timezone = models.CharField(max_length=100, default="UTC")

    default_event_duration = models.PositiveIntegerField(default=60)
    default_reminders = models.JSONField(default=list, blank=True)

    working_hours_enabled = models.BooleanField(default=False)
    working_hours_start = models.TimeField(null=True, blank=True)
    working_hours_end = models.TimeField(null=True, blank=True)
    working_days = models.JSONField(default=list, blank=True)

    email_notifications_enabled = models.BooleanField(default=True)
    notification_preferences = models.JSONField(default=dict, blank=True)

    show_declined_events = models.BooleanField(default=False)
    auto_add_invitations = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Calendar settings"
        indexes = [
            models.Index(fields=["organization", "user"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="unique_calendar_settings_per_user_per_org")
        ]

    def __str__(self):
        return f"Settings for {getattr(self.user, 'email', self.user_id)}"

    def clean(self):
        super().clean()
        user_org_id = _user_org_id(self.user)
        if user_org_id and self.organization_id and user_org_id != self.organization_id:
            raise ValidationError({"organization": "CalendarSettings.organization must match user.organization"})

    def save(self, *args, validate: bool = True, **kwargs):
        if validate:
            self.full_clean()
        super().save(*args, **kwargs)


# -----------------------------
# Notifications (in-app activity feed)
# -----------------------------
class Notification(TimeStampedModel):
    """
    In-app notifications for calendar events and updates.
    Separate from EventReminder (scheduled alerts).
    """

    NOTIFICATION_TYPE_CHOICES = [
        ("event_reminder", "Event reminder"),
        ("event_invitation", "Event invitation"),
        ("event_updated", "Event updated"),
        ("event_cancelled", "Event cancelled"),
        ("event_response", "Attendee response"),
        ("calendar_shared", "Calendar shared with you"),
        ("calendar_unshared", "Calendar access removed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="calendar_notifications",
        help_text="Tenant boundary. Must match user.organization and related objects org.",
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="calendar_notifications")

    notification_type = models.CharField(max_length=30, choices=NOTIFICATION_TYPE_CHOICES)
    title = models.CharField(max_length=255)
    message = models.TextField()

    event = models.ForeignKey(Event, on_delete=models.CASCADE, null=True, blank=True, related_name="notifications")
    calendar = models.ForeignKey(Calendar, on_delete=models.CASCADE, null=True, blank=True, related_name="notifications")

    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    action_url = models.URLField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "user", "is_read", "created_at"]),
            models.Index(fields=["organization", "user", "notification_type"]),
            models.Index(fields=["organization", "event", "user"]),
        ]

    def __str__(self):
        return f"{self.get_notification_type_display()} for {getattr(self.user, 'email', self.user_id)}"

    def clean(self):
        super().clean()

        user_org_id = _user_org_id(self.user)
        if user_org_id and self.organization_id and user_org_id != self.organization_id:
            raise ValidationError({"organization": "Notification.organization must match user.organization"})

        if self.event_id and self.organization_id and self.event.organization_id != self.organization_id:
            raise ValidationError({"event": "event.organization must match Notification.organization"})
        if self.calendar_id and self.organization_id and self.calendar.organization_id != self.organization_id:
            raise ValidationError({"calendar": "calendar.organization must match Notification.organization"})

    def mark_as_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at"])

    def save(self, *args, validate: bool = True, **kwargs):
        if validate:
            self.full_clean()
        super().save(*args, **kwargs)


# -----------------------------
# Calendar Event (System Projection)
# -----------------------------
class CalendarEvent(TimeStampedModel):
    """
    CalendarEvent is a read-only projection automatically generated by the system from Decision and Task.
    Users are not allowed to manually create or modify, they can only view and click to jump to the source entity.
    When the source entity is deleted, the corresponding CalendarEvent will also be deleted in cascade.
    """

    class EventType(models.TextChoices):
        DECISION = 'decision', 'Decision Event'
        TASK = 'task', 'Task Event'
        DECISION_REVIEW = 'decision_review', 'Decision Review Event'

    # ── Owning organization (multi-tenant isolation, consistent with existing model) ────────
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='calendar_events',
        help_text="Tenant boundary. Must match source entity's organization.",
    )

    # ── Event type ──────────────────────────────────────────
    event_type = models.CharField(
        max_length=20,
        choices=EventType.choices,
        help_text="Type of calendar event derived from source entity",
    )

    # ── Time (derived from the source entity, written by the system, not allowed to be modified by the user) ────
    start_time = models.DateTimeField(
        help_text="Event start time, derived from source entity (e.g. committed_at / due_date)",
    )
    end_time = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Event end time (optional)",
    )

    # ── Display fields ──────────────────────────────────────────
    title = models.CharField(
        max_length=255,
        help_text="Display title shown on the calendar",
    )

    # ── Related Decision ─────────────────────────────────────
    decision = models.ForeignKey(
        'decision.Decision',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='calendar_events',
        help_text="Source Decision (required for decision / decision_review events)",
    )

    # ── Associate Task ─────────────────────────────────────────
    task = models.ForeignKey(
        'task.Task',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='calendar_events',
        help_text="Source Task (required for task events)",
    )

    # ── Associate Review (accurately track the source of Review Event) ──────────
    review = models.ForeignKey(
        'decision.Review',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='calendar_events',
        help_text="Source Review (used for decision_review events)",
    )

    class Meta:
        db_table = 'calendar_derived_events'
        ordering = ['start_time']
        indexes = [
            # The front end uses this index when filtering by time range + type
            models.Index(fields=['organization', 'start_time', 'event_type']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['event_type', 'task'],
                condition=models.Q(task__isnull=False),
                name='unique_cal_event_per_task_type',
            ),
        ]

    def __str__(self):
        return f"[{self.event_type}] {self.title} @ {self.start_time}"

    def clean(self):
        """
        Verify that each event type must be associated with the correct source entity, events cannot stand alone"""
        super().clean()

        if self.event_type == self.EventType.DECISION:
            if not self.decision_id:
                raise ValidationError(
                    {"decision": "Decision Event must be linked to a Decision."}
                )

        elif self.event_type == self.EventType.TASK:
            if not self.task_id:
                raise ValidationError(
                    {"task": "Task Event must be linked to a Task."}
                )

        elif self.event_type == self.EventType.DECISION_REVIEW:
            if not self.decision_id:
                raise ValidationError(
                    {"decision": "Decision Review Event must be linked to a Decision."}
                )

    def save(self, *args, validate: bool = True, **kwargs):
        if validate:
            self.full_clean()
        super().save(*args, **kwargs)


# -----------------------------
# Booking Link
# -----------------------------
class BookingLink(TimeStampedModel):
    """
    A shareable link that lets someone book time with one user.

    `owner` is the host - whose availability is published, whose calendar the
    booking lands on, and whose Google connection and CalendarSettings are
    read. `created_by` is whoever made the link, which may be a colleague
    arranging time on the host's behalf.

    Tenant-scoped like the rest of this app, so a public request must resolve
    the organisation before it can read one. That is why the public URL carries
    the org slug (/book/<org>/<slug>) rather than an opaque token: the org has
    to be known up front to select the right schema.

    Availability rules live here as plain values so they can be handed straight
    to calendars.availability without the booking flow needing the model.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Multi-tenancy boundary
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="booking_links",
        help_text="Tenant boundary. Must match the owner's organization.",
    )

    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="booking_links",
        help_text="The host: the person whose time is being booked.",
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_booking_links",
        help_text=(
            "Who set the link up. Differs from owner when a colleague arranges "
            "time on the host's behalf. Null once that account is removed - the "
            "link belongs to the host, so it outlives its creator."
        ),
    )

    calendar = models.ForeignKey(
        Calendar,
        on_delete=models.CASCADE,
        related_name="booking_links",
        help_text="Calendar that bookings are written to.",
    )

    # Who the link was made for. Optional and plural: a link can be sent to
    # several people, or posted with nobody in particular in mind.
    invitee_users = models.ManyToManyField(
        User,
        blank=True,
        related_name="invited_booking_links",
        help_text=(
            "Intended guests who already have an account here. Each is notified "
            "in-app, and a booking they make is tied back to them."
        ),
    )
    invitee_emails = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Intended guests with no account, as a list of addresses. Kept "
            "separate from invitee_users so a guest who later registers is not "
            "silently promoted to an account they may not own."
        ),
    )

    slug = models.SlugField(
        max_length=100,
        help_text="URL segment, unique within the organisation.",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    # Scheduling rules — mirror calendars.availability.BookingRules.
    duration_minutes = models.PositiveIntegerField(default=30)
    slot_increment_minutes = models.PositiveIntegerField(default=15)
    buffer_before_minutes = models.PositiveIntegerField(default=0)
    buffer_after_minutes = models.PositiveIntegerField(default=0)
    min_notice_minutes = models.PositiveIntegerField(default=60)
    max_advance_days = models.PositiveIntegerField(default=60)

    timezone = models.CharField(
        max_length=100,
        default="UTC",
        help_text="Owner's timezone; availability windows are wall-clock in this zone.",
    )

    availability_windows = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Weekly windows as [{'weekday': 0-6 (Mon-Sun), 'start': 'HH:MM', "
            "'end': 'HH:MM'}]. Empty falls back to the owner's CalendarSettings "
            "working hours."
        ),
    )

    is_active = models.BooleanField(default=True)
    # Default stays open: anyone with the URL can book. Turning this on
    # restricts the public write to people named on the link, signed in.
    invitees_only = models.BooleanField(
        default=False,
        help_text=(
            "When set, only named invitees who are signed in can book. "
            "Guests and anyone else with the URL cannot."
        ),
    )

    class Meta:
        ordering = ["title"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(is_deleted=False),
                name="uniq_booking_link_org_slug",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "slug"]),
        ]

    def __str__(self):
        return f"{self.slug} ({self.title})"

    def clean(self):
        super().clean()

        if self.duration_minutes <= 0:
            raise ValidationError({"duration_minutes": "Duration must be positive."})
        if self.slot_increment_minutes <= 0:
            raise ValidationError(
                {"slot_increment_minutes": "Slot increment must be positive."}
            )
        if self.max_advance_days <= 0:
            raise ValidationError(
                {"max_advance_days": "Booking horizon must be at least one day."}
            )

        # The calendar receives the bookings, so it must sit in the same tenant.
        if self.calendar_id and self.calendar.organization_id != self.organization_id:
            raise ValidationError(
                {"calendar": "Calendar must belong to the same organization."}
            )

        for window in self.availability_windows or []:
            if not isinstance(window, dict):
                raise ValidationError(
                    {"availability_windows": "Each window must be an object."}
                )
            missing = {"weekday", "start", "end"} - set(window)
            if missing:
                raise ValidationError(
                    {
                        "availability_windows":
                            f"Window is missing {', '.join(sorted(missing))}."
                    }
                )
            if not isinstance(window["weekday"], int) or not 0 <= window["weekday"] <= 6:
                raise ValidationError(
                    {"availability_windows": "weekday must be an integer 0-6."}
                )
            try:
                start = datetime.strptime(window["start"], "%H:%M").time()
                end = datetime.strptime(window["end"], "%H:%M").time()
            except (TypeError, ValueError):
                raise ValidationError(
                    {"availability_windows": "start and end must be 'HH:MM'."}
                )
            if start >= end:
                raise ValidationError(
                    {"availability_windows": "Window start must precede its end."}
                )

    def save(self, *args, validate: bool = True, **kwargs):
        if validate:
            self.full_clean()
        super().save(*args, **kwargs)

from __future__ import annotations

from typing import Any
from types import SimpleNamespace
from datetime import datetime, timedelta, date

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import generics, status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from core.authentication import TenantAwareJWTAuthentication
from .services import (
    get_calendar_events,
    modify_single_occurrence,
    cancel_single_occurrence,
    split_series_from_occurrence,
    _count_occurrences_before,
    # Occurrence expansion moved to services so booking availability can reuse
    # it without importing the API layer. Re-exported here: existing callers
    # (including tests) still import these names from views.
    _events_intersecting_range,
    _expand_recurring_event,
    get_busy_intervals_by_calendar,
    # Booking links
    rules_from_booking_link,
    schedule_from_booking_link,
)

from django.contrib.auth import get_user_model

from core.models import Organization, ProjectMember
from core.slug_mixins import resolve_project_pk
from core.services.tenant import slug_to_schema_name
from core.tenant_context import tenant_schema_context
from .models import (
    BookingLink,
    Calendar,
    CalendarSettings,
    CalendarShare,
    CalendarSubscription,
    Event,
    EventAttendee,
    RecurrenceException,
    EventReminder,
)
from .permissions import (
    IsAuthenticatedInOrganization,
    CalendarAccessPermission,
    EventAccessPermission,
    SubscriptionOwnerPermission,
    get_user_organization,
)
from .serializers import (
    CalendarSerializer,
    CalendarCreateUpdateSerializer,
    CalendarShareSerializer,
    CalendarShareRequestSerializer,
    CalendarSubscriptionSerializer,
    CalendarSubscriptionRequestSerializer,
    EventSerializer,
    EventCreateUpdateSerializer,
    EventAttendeeSerializer,
    AttendeeCreateRequestSerializer,
    AttendeeResponseRequestSerializer,
    EventReminderSerializer,
    # Booking links
    PublicBookingLinkSerializer,
    BookingRequestSerializer,
    BookingLookupSerializer,
    BookingLinkSerializer,
)
from urllib.parse import quote

from django.http import HttpResponse
from django.urls import reverse

from .booking_access import booker_shares_project, can_book_public_link
from .booking_ics import as_webcal_url, build_booking_ics
from .booking_lookup import find_guest_bookings, find_viewer_bookings, serialize_viewer_bookings
from .booking_write import (
    calendars_for_booking_availability,
    cancel_booking_events,
    create_booking_events,
    event_belongs_to_booking_link,
    ensure_personal_calendar,
    is_team_booking_calendar,
    lock_booking_event,
    prefer_visible_booking_copy,
    sync_booking_siblings,
)
from .tasks import send_booking_confirmation_task, send_booking_recovery_task, queue_booking_task
from .booking_notifications import (
    notify_booking_cancelled,
    notify_booking_made,
    notify_booking_rescheduled,
    notify_link_created,
)
from .booking_invite_state import (
    find_upcoming_guest_booking,
    mark_invite_booked,
    mark_invite_unbooked,
)
from .booking_tokens import make_cancel_token, read_cancel_token, make_feed_token, read_feed_token
from .exceptions import calendar_error_response
from google_calendar_integration.tasks import export_event_to_google_task
from google_calendar_integration.services import (
    GoogleAvailabilityUnavailable,
    get_merged_availability,
    is_slot_still_available,
)

User = get_user_model()


class CalendarViewSet(viewsets.ModelViewSet):
    """
    Calendar CRUD aligned with `/calendars/` endpoints.

    - `list`: calendars owned by or shared with the user (optionally including subscriptions)
    - `create`: create new calendar for current user and organization
    - `retrieve/update/destroy`: calendar details, soft delete on destroy
    """

    serializer_class = CalendarSerializer
    permission_classes = [IsAuthenticatedInOrganization, CalendarAccessPermission]
    required_permission = "view_all"

    def initial(self, request, *args, **kwargs):
        if getattr(self, "action", None) in {"update", "partial_update", "destroy"}:
            self.required_permission = "manage"
        else:
            self.required_permission = "view_all"
        super().initial(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        organization = get_user_organization(user)
        if not organization:
            return Calendar.objects.none()

        visibility = self.request.query_params.get("visibility")
        project_id_param = self.request.query_params.get("project_id")
        include_subscriptions_param = self.request.query_params.get("include_subscriptions", "true")
        include_subscriptions = include_subscriptions_param.lower() != "false"

        owned_qs = Calendar.objects.filter(
            organization=organization,
            owner=user,
            is_deleted=False,
        )

        shared_calendar_ids = CalendarShare.objects.filter(
            organization=organization,
            shared_with=user,
            is_deleted=False,
        ).values_list("calendar_id", flat=True)

        legacy_qs = Calendar.objects.filter(
            Q(pk__in=owned_qs.values_list("pk", flat=True))
            | Q(pk__in=shared_calendar_ids)
        ).filter(is_deleted=False, organization=organization)

        if include_subscriptions:
            subscribed_calendar_ids = CalendarSubscription.objects.filter(
                organization=organization,
                user=user,
                is_deleted=False,
                calendar__isnull=False,
            ).values_list("calendar_id", flat=True)
            legacy_qs = legacy_qs | Calendar.objects.filter(
                pk__in=subscribed_calendar_ids,
                organization=organization,
                is_deleted=False,
            )

        project_ids = ProjectMember.objects.filter(
            user=user,
            is_active=True,
        ).values_list("project_id", flat=True)
        project_qs = Calendar.objects.filter(
            project_id__in=project_ids,
            is_deleted=False,
        )

        qs = legacy_qs | project_qs

        if visibility:
            qs = qs.filter(visibility=visibility)

        if project_id_param:
            project_id = resolve_project_pk(project_id_param)
            if project_id is None:
                return Calendar.objects.none()
            qs = qs.filter(
                Q(project_id=project_id) | Q(project__isnull=True, owner=user)
            )

        return qs.distinct().order_by("-is_primary", "name")

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return CalendarCreateUpdateSerializer
        return CalendarSerializer

    def perform_create(self, serializer):
        user = self.request.user
        organization = get_user_organization(user)
        if not organization:
            raise ValueError("Organization context is required to create calendars.")
        serializer.save(owner=user, organization=organization)

    def perform_destroy(self, instance: Calendar):
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted", "updated_at"])


class CalendarShareListCreateView(generics.ListCreateAPIView):
    """
    List and create calendar shares for a given calendar.
    """

    permission_classes = [IsAuthenticatedInOrganization, CalendarAccessPermission]
    required_permission = "manage"
    serializer_class = CalendarShareSerializer

    def get_calendar(self) -> Calendar:
        user = self.request.user
        organization = get_user_organization(user)
        calendar_id = self.kwargs["calendar_id"]
        calendar = get_object_or_404(
            Calendar, id=calendar_id, organization=organization, is_deleted=False
        )
        self.check_object_permissions(self.request, calendar)
        return calendar

    def get_queryset(self):
        calendar = self.get_calendar()
        return CalendarShare.objects.filter(
            organization=calendar.organization,
            calendar=calendar,
            is_deleted=False,
        )

    def get(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request, *args, **kwargs):
        calendar = self.get_calendar()

        request_serializer = CalendarShareRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        data = request_serializer.validated_data

        user_model = self.request.user.__class__
        shared_with = get_object_or_404(
            user_model,
            id=data["user_id"],
            organization_id=calendar.organization_id,
        )

        share, _created = CalendarShare.objects.update_or_create(
            organization=calendar.organization,
            calendar=calendar,
            shared_with=shared_with,
            defaults={
                "permission": data["permission"],
                "can_invite_others": data.get("can_invite_others", False),
            },
        )

        serializer = self.get_serializer(share)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class CalendarShareDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    Retrieve, update or delete a calendar share.
    """

    permission_classes = [IsAuthenticatedInOrganization, CalendarAccessPermission]
    required_permission = "manage"
    serializer_class = CalendarShareSerializer
    lookup_url_kwarg = "share_id"

    def get_object(self):
        user = self.request.user
        organization = get_user_organization(user)
        calendar_id = self.kwargs["calendar_id"]
        share_id = self.kwargs["share_id"]

        calendar = get_object_or_404(
            Calendar, id=calendar_id, organization=organization, is_deleted=False
        )
        self.check_object_permissions(self.request, calendar)

        share = get_object_or_404(
            CalendarShare,
            id=share_id,
            organization=organization,
            calendar=calendar,
            is_deleted=False,
        )
        return share

    def perform_destroy(self, instance: CalendarShare):
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted", "updated_at"])


class SubscriptionListCreateView(generics.ListCreateAPIView):
    """
    List and create calendar subscriptions.
    """

    permission_classes = [IsAuthenticatedInOrganization, IsAuthenticated]
    serializer_class = CalendarSubscriptionSerializer

    def get_queryset(self):
        user = self.request.user
        organization = get_user_organization(user)
        if not organization:
            return CalendarSubscription.objects.none()

        return CalendarSubscription.objects.filter(
            organization=organization,
            user=user,
            is_deleted=False,
        ).select_related("calendar")

    def get(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request, *args, **kwargs):
        user = self.request.user
        organization = get_user_organization(user)
        if not organization:
            return Response(
                {"error": "Organization context is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        request_serializer = CalendarSubscriptionRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        data = request_serializer.validated_data

        calendar = None
        if "calendar_id" in data:
            calendar = get_object_or_404(
                Calendar,
                id=data["calendar_id"],
                organization=organization,
                is_deleted=False,
            )

        subscription, _created = CalendarSubscription.objects.update_or_create(
            organization=organization,
            user=user,
            calendar=calendar,
            source_url=data.get("source_url"),
            defaults={
                "color_override": data.get("color_override"),
                "is_hidden": data.get("is_hidden", False),
                "notification_enabled": data.get("notification_enabled", True),
            },
        )

        serializer = self.get_serializer(subscription)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SubscriptionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    Retrieve, update or delete a subscription.
    """

    permission_classes = [IsAuthenticatedInOrganization, SubscriptionOwnerPermission]
    serializer_class = CalendarSubscriptionSerializer
    lookup_url_kwarg = "subscription_id"

    def get_object(self):
        user = self.request.user
        organization = get_user_organization(user)
        subscription_id = self.kwargs["subscription_id"]

        subscription = get_object_or_404(
            CalendarSubscription,
            id=subscription_id,
            organization=organization,
            is_deleted=False,
        )
        self.check_object_permissions(self.request, subscription)
        return subscription

    def patch(self, request, *args, **kwargs):
        subscription = self.get_object()

        partial_data = {
            key: request.data.get(key)
            for key in ["color_override", "is_hidden", "notification_enabled"]
            if key in request.data
        }
        for key, value in partial_data.items():
            setattr(subscription, key, value)
        subscription.save()

        serializer = self.get_serializer(subscription)
        return Response(serializer.data)

    def perform_destroy(self, instance: CalendarSubscription):
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted", "updated_at"])


class EventViewSet(viewsets.ModelViewSet):
    """
    Event CRUD aligned with `/events/` endpoints.
    """

    serializer_class = EventSerializer
    permission_classes = [IsAuthenticatedInOrganization, EventAccessPermission]
    required_permission = "view_all"

    def initial(self, request, *args, **kwargs):
        if getattr(self, "action", None) in {"create", "update", "partial_update", "destroy"}:
            self.required_permission = "edit"
        else:
            self.required_permission = "view_all"
        super().initial(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        project_id_param = self.request.query_params.get("project_id")
        project_id = None
        if project_id_param:
            project_id = resolve_project_pk(project_id_param)
            if project_id is None:
                return Event.objects.none()

        calendars = _get_accessible_calendars(user, project_id=project_id)
        queryset = (
            Event.objects.select_related("calendar", "created_by")
            .filter(
                _visible_events_q(user, calendars, get_user_organization(user)),
                is_deleted=False,
            )
            .distinct()
        )

        calendar_ids_param = self.request.query_params.get("calendar_ids")
        if calendar_ids_param:
            calendar_ids = [cid for cid in calendar_ids_param.split(",") if cid]
            queryset = queryset.filter(calendar_id__in=calendar_ids)

        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)

        event_type = self.request.query_params.get("event_type")
        if event_type:
            queryset = queryset.filter(event_type=event_type)

        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search)
                | Q(description__icontains=search)
                | Q(location__icontains=search)
            )

        start_min = self.request.query_params.get("start_min")
        if start_min:
            queryset = queryset.filter(start_datetime__gte=start_min)

        start_max = self.request.query_params.get("start_max")
        if start_max:
            queryset = queryset.filter(start_datetime__lte=start_max)

        time_min = self.request.query_params.get("time_min")
        if time_min:
            queryset = queryset.filter(end_datetime__gt=time_min)

        time_max = self.request.query_params.get("time_max")
        if time_max:
            queryset = queryset.filter(start_datetime__lt=time_max)

        return queryset.order_by("start_datetime")

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return EventCreateUpdateSerializer
        return EventSerializer

    def create(self, request, *args, **kwargs):
        """
        Create event and return full Event representation (with id, calendar_id, etc.).
        """
        serializer = EventCreateUpdateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        target_calendar = get_object_or_404(
            Calendar,
            id=serializer.validated_data["calendar_id"],
            is_deleted=False,
        )
        perm = CalendarAccessPermission()
        self.required_permission = "edit"
        if not perm.has_object_permission(request, self, target_calendar):
            raise PermissionDenied("You do not have permission to create events on this calendar.")

        event = serializer.save(
            calendar=target_calendar,
            organization=target_calendar.organization,
            created_by=request.user if request.user.is_authenticated else None,
        )
        eid = str(event.id)
        self._queue_google_export(eid)
        output_serializer = EventSerializer(event)
        headers = self.get_success_headers(output_serializer.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        """
        Update event and return full Event representation.
        """
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        if (instance.metadata or {}).get("source") == "booking_link":
            instance = lock_booking_event(instance)
            if instance.is_deleted or instance.status == "cancelled":
                return calendar_error_response("BOOKING_CANCELLED", "This booking has been cancelled.", status_code=409)
            if request.data.get("calendar_id") and str(request.data["calendar_id"]) != str(instance.calendar_id):
                return calendar_error_response("BAD_REQUEST", "A booking cannot be moved to another calendar.", status_code=400)

        # ETag / If-Match handling (optimistic concurrency)
        if_match = request.META.get("HTTP_IF_MATCH")
        if if_match is not None and instance.etag and if_match != instance.etag:
            return calendar_error_response(
                error="PRECONDITION_FAILED",
                message="ETag mismatch. Resource has been modified.",
                status_code=status.HTTP_412_PRECONDITION_FAILED,
            )

        serializer = EventCreateUpdateSerializer(
            instance, data=request.data, partial=partial, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        event = serializer.save()
        if (event.metadata or {}).get("source") == "booking_link" and event.status == "cancelled":
            self.perform_destroy(event)
        for sibling in sync_booking_siblings(event):
            self._queue_google_export(str(sibling.id))
        eid = str(event.id)
        self._queue_google_export(eid)
        output_serializer = EventSerializer(event)
        return Response(output_serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def _queue_google_export(self, event_id: str) -> None:
        """
        Hand the worker the schema explicitly.

        A Celery worker never passes through TenantSchemaMiddleware, so the
        default of 'public' silently finds nothing for an org with its own
        schema - the export, and the delete that shares this path, would both
        no-op there.
        """
        organization = get_user_organization(self.request.user)
        schema = slug_to_schema_name(organization.slug) if organization else "public"
        queue_booking_task(export_event_to_google_task, event_id, tenant_schema=schema)

    @transaction.atomic
    def perform_destroy(self, instance: Event):
        instance = lock_booking_event(instance)
        if instance.is_deleted:
            return
        eid = str(instance.id)
        # Read the attendees before the row is marked deleted.
        booked_guest = (
            EventAttendee.objects.filter(
                event=instance,
                is_organizer=False,
                metadata__source="booking_link",
                is_deleted=False,
            )
            .exclude(user=None)
            .first()
        )
        organizer = (
            EventAttendee.objects.filter(event=instance, is_organizer=True)
            .exclude(user=None)
            .first()
        )

        is_booking = (
            bool(booked_guest)
            or (instance.metadata or {}).get("source") == "booking_link"
        )
        # Older bookings dual-wrote a personal row and a project mirror.
        # Deleting one still cancels the group. Ordinary events only
        # soft-delete the row that was asked for.
        if is_booking:
            siblings = cancel_booking_events(instance)
        else:
            instance.is_deleted = True
            instance.save(update_fields=["is_deleted", "updated_at"])
            siblings = [instance]

        # Only bookings, and only guests with an account. Ordinary events are
        # deleted here too, and announcing every one of those would be noise -
        # while a guest whose meeting just vanished genuinely needs telling.
        if booked_guest:
            notify_booking_cancelled(
                instance,
                host_id=organizer.user_id if organizer else instance.created_by_id,
                guest_user_id=booked_guest.user_id,
                actor=self.request.user,
                by_guest=False,
            )
            slug = (instance.metadata or {}).get("booking_link_slug") or (
                (booked_guest.metadata or {}).get("booking_link_slug")
            )
            if slug:
                from .models import BookingLink

                link = BookingLink.objects.filter(
                    organization=instance.organization,
                    slug=slug,
                    is_deleted=False,
                ).first()
                if link:
                    mark_invite_unbooked(link, booked_guest.user)
        exported = {eid}
        for sibling in siblings:
            exported.add(str(sibling.pk))
        for event_id in exported:
            self._queue_google_export(event_id)


class EventSearchView(generics.ListAPIView):
    """
    Event search endpoint backed by the same Event model.
    """

    serializer_class = EventSerializer
    permission_classes = [IsAuthenticatedInOrganization, EventAccessPermission]

    def get_queryset(self):
        user = self.request.user
        project_id_param = self.request.query_params.get("project_id")
        project_id = None
        if project_id_param:
            project_id = resolve_project_pk(project_id_param)
            if project_id is None:
                return Event.objects.none()

        calendars = _get_accessible_calendars(user, project_id=project_id)
        queryset = (
            Event.objects.select_related("calendar", "created_by")
            .filter(
                _visible_events_q(user, calendars, get_user_organization(user)),
                is_deleted=False,
            )
            .distinct()
        )

        q = self.request.query_params.get("q")
        if not q or len(q.strip()) < 2:
            return Event.objects.none()
        q = q.strip()

        queryset = queryset.filter(
            Q(title__icontains=q)
            | Q(description__icontains=q)
            | Q(location__icontains=q)
        )

        calendar_ids_param = self.request.query_params.get("calendar_ids")
        if calendar_ids_param:
            calendar_ids = [cid for cid in calendar_ids_param.split(",") if cid]
            queryset = queryset.filter(calendar_id__in=calendar_ids)

        time_min = self.request.query_params.get("time_min")
        if time_min:
            queryset = queryset.filter(end_datetime__gt=time_min)

        time_max = self.request.query_params.get("time_max")
        if time_max:
            queryset = queryset.filter(start_datetime__lt=time_max)

        return queryset.order_by("start_datetime")


def _parse_iso_datetime(value: str):
    dt = parse_datetime(value)
    if dt is None:
        raise ValueError("Invalid datetime format")
    if timezone.is_naive(dt):
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        raise ValueError("Invalid date format, expected YYYY-MM-DD")


def _get_accessible_calendars(
    user,
    calendar_ids: list[str] | None = None,
    project_id: int | None = None,
):
    organization = get_user_organization(user)
    qs = Calendar.objects.none()

    if organization:
        owned = Calendar.objects.filter(
            organization=organization,
            owner=user,
            is_deleted=False,
        )
        shared_ids = CalendarShare.objects.filter(
            organization=organization,
            shared_with=user,
            is_deleted=False,
        ).values_list("calendar_id", flat=True)
        subscribed_ids = CalendarSubscription.objects.filter(
            organization=organization,
            user=user,
            is_deleted=False,
            calendar__isnull=False,
            is_hidden=False,
        ).values_list("calendar_id", flat=True)

        legacy_qs = Calendar.objects.filter(
            organization=organization,
            is_deleted=False,
        ).filter(
            Q(pk__in=owned.values_list("pk", flat=True))
            | Q(pk__in=shared_ids)
            | Q(pk__in=subscribed_ids)
        )
        qs = qs | legacy_qs

    project_ids = ProjectMember.objects.filter(
        user=user,
        is_active=True,
    ).values_list("project_id", flat=True)
    project_qs = Calendar.objects.filter(
        project_id__in=project_ids,
        is_deleted=False,
    )
    qs = qs | project_qs

    if calendar_ids:
        qs = qs.filter(id__in=calendar_ids)
    if project_id is not None:
        # Project week view still needs the team calendar, but personal
        # booking / events live on the user's own calendar in the same org.
        qs = qs.filter(Q(project_id=project_id) | Q(project__isnull=True, owner=user))

    return qs.distinct()



def _visible_events_q(user, calendars, organization) -> Q:
    """
    Events a user may see: on a calendar they can reach, or one they attend.

    Attendance conferring visibility is what lets someone who booked time find
    that meeting in their own week. The event lives on the host's calendar, so
    without this clause an attendee is invisible to themselves.

    The organisation is pinned explicitly on the attendee branch. Orgs without
    their own schema share `public`, where an unscoped attendee lookup would
    reach across tenants.
    """
    attending = Q(attendees__user=user, attendees__is_deleted=False)
    if organization is not None:
        attending &= Q(organization=organization)
    return Q(calendar__in=calendars) | attending


def _events_on_selected_calendars(events, selected_ids: set[str], accessible_ids: set[str]):
    """
    Opening one calendar should show that diary, not every meeting the
    viewer attends.

    A guest who cannot open the host calendar still sees the booking until
    they have their own copy on a calendar they can open.
    """
    groups_on_accessible = {
        (getattr(event, "metadata", None) or {}).get("booking_group")
        for event in events
        if (getattr(event, "metadata", None) or {}).get("booking_group")
        and str(getattr(event, "calendar_id", "")) in accessible_ids
    }
    kept = []
    for event in events:
        calendar_id = str(getattr(event, "calendar_id", ""))
        if calendar_id in selected_ids:
            kept.append(event)
            continue
        group = (getattr(event, "metadata", None) or {}).get("booking_group")
        if group and group in groups_on_accessible:
            continue
        if calendar_id not in accessible_ids:
            kept.append(event)
    return kept


def _build_calendar_view_payload(
    user,
    start_dt,
    end_dt,
    calendar_ids: list[str] | None,
    project_id: int | None,
    view_type: str,
):
    accessible_all = _get_accessible_calendars(user, project_id=project_id)
    calendars = _get_accessible_calendars(user, calendar_ids, project_id=project_id)
    organization = get_user_organization(user)

    events_qs = _events_intersecting_range(
        start_dt,
        end_dt,
        Event.objects.select_related("calendar", "created_by", "recurrence_rule")
        .filter(
            _visible_events_q(user, accessible_all, organization),
            is_deleted=False,
        )
        .distinct(),
    )

    instances: list[Any] = []
    for ev in events_qs:
        if ev.is_recurring and ev.recurrence_rule_id:
            instances.extend(_expand_recurring_event(ev, start_dt, end_dt))
        else:
            instances.append(ev)

    if calendar_ids:
        instances = _events_on_selected_calendars(
            instances,
            selected_ids={str(calendar.id) for calendar in calendars},
            accessible_ids={str(calendar.id) for calendar in accessible_all},
        )

    # A booking is two rows (host primary + project copy). Showing both on the
    # same week view looks like a double-booked slot, so keep one card.
    instances = prefer_visible_booking_copy(
        instances,
        visible_calendar_ids=[calendar.id for calendar in calendars],
    )

    events_data = EventSerializer(instances, many=True).data

    # Query the CalendarEvent automatically generated by the system (from Task / Decision)
    from .models import CalendarEvent
    from .serializers import CalendarEventSerializer

    organization = get_user_organization(user)
    from .services import get_calendar_events

    derived_qs = get_calendar_events(
        organization=organization,
        start=start_dt.isoformat(),
        end=end_dt.isoformat(),
        project_id=str(project_id) if project_id is not None else None,
    ).filter(is_deleted=False)
    derived_data = CalendarEventSerializer(derived_qs, many=True).data

    # Combine two types of events
    events_data = list(events_data) + list(derived_data)
    calendars_data = CalendarSerializer(calendars, many=True).data

    return {
        "view_type": view_type,
        "start_date": start_dt.isoformat().replace("+00:00", "Z"),
        "end_date": end_dt.isoformat().replace("+00:00", "Z"),
        "events": events_data,
        "calendars": calendars_data,
    }



class EventInstancesView(generics.ListAPIView):
    """
    Return expanded instances for a recurring event.
    """

    serializer_class = EventSerializer
    permission_classes = [IsAuthenticatedInOrganization, EventAccessPermission]

    def get(self, request, *args, **kwargs):
        event_id = self.kwargs["event_id"]
        event = get_object_or_404(
            Event,
            id=event_id,
            is_deleted=False,
        )

        # Object-level permission
        perm = EventAccessPermission()
        setattr(self, "required_permission", "view_all")
        if not perm.has_object_permission(request, self, event):
            raise PermissionDenied("You do not have access to this event.")

        time_min_raw = request.query_params.get("time_min")
        time_max_raw = request.query_params.get("time_max")
        max_results_raw = request.query_params.get("max_results")

        if not time_min_raw or not time_max_raw:
            return Response(
                {"detail": "time_min and time_max are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            time_min = _parse_iso_datetime(time_min_raw)
            time_max = _parse_iso_datetime(time_max_raw)
        except ValueError:
            return Response(
                {"detail": "Invalid datetime format for time_min or time_max."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if time_min >= time_max:
            return Response(
                {"detail": "time_min must be earlier than time_max."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_results = 250
        if max_results_raw is not None:
            try:
                max_results = max(min(int(max_results_raw), 2500), 1)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "max_results must be an integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        instances = _expand_recurring_event(event, time_min, time_max, max_results)
        serializer = self.get_serializer(instances, many=True)
        return Response(serializer.data)


class DayView(generics.GenericAPIView):
    """
    Day view: events for a specific date.
    """

    permission_classes = [IsAuthenticatedInOrganization]

    def get(self, request, *args, **kwargs):
        date_str = request.query_params.get("date")
        if not date_str:
            return calendar_error_response(
                "BAD_REQUEST",
                "date query parameter is required.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        try:
            d = _parse_date(date_str)
        except ValueError as exc:
            return calendar_error_response(
                "INVALID_DATETIME",
                str(exc),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        start_dt = timezone.make_aware(datetime.combine(d, datetime.min.time()), timezone.utc)
        end_dt = start_dt + timedelta(days=1)

        calendar_ids_param = request.query_params.get("calendar_ids")
        calendar_ids = calendar_ids_param.split(",") if calendar_ids_param else None
        project_id_param = request.query_params.get("project_id")
        project_id = resolve_project_pk(project_id_param)

        payload = _build_calendar_view_payload(
            request.user,
            start_dt,
            end_dt,
            calendar_ids,
            project_id,
            view_type="day",
        )
        if isinstance(payload, Response):
            return payload
        return Response(payload)


class WeekView(generics.GenericAPIView):
    """
    Week view starting from start_date.
    """

    permission_classes = [IsAuthenticatedInOrganization]

    def get(self, request, *args, **kwargs):
        start_str = request.query_params.get("start_date")
        if not start_str:
            return calendar_error_response(
                "BAD_REQUEST",
                "start_date query parameter is required.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        try:
            d = _parse_date(start_str)
        except ValueError as exc:
            return calendar_error_response(
                "INVALID_DATETIME",
                str(exc),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        start_dt = timezone.make_aware(datetime.combine(d, datetime.min.time()), timezone.utc)
        end_dt = start_dt + timedelta(days=7)

        calendar_ids_param = request.query_params.get("calendar_ids")
        calendar_ids = calendar_ids_param.split(",") if calendar_ids_param else None
        project_id_param = request.query_params.get("project_id")
        project_id = resolve_project_pk(project_id_param)

        payload = _build_calendar_view_payload(
            request.user,
            start_dt,
            end_dt,
            calendar_ids,
            project_id,
            view_type="week",
        )
        if isinstance(payload, Response):
            return payload
        return Response(payload)


class MonthView(generics.GenericAPIView):
    """
    Month view for a given year and month.
    """

    permission_classes = [IsAuthenticatedInOrganization]

    def get(self, request, *args, **kwargs):
        year_str = request.query_params.get("year")
        month_str = request.query_params.get("month")
        if not year_str or not month_str:
            return calendar_error_response(
                "BAD_REQUEST",
                "year and month query parameters are required.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        try:
            year = int(year_str)
            month = int(month_str)
            d = date(year=year, month=month, day=1)
        except Exception:
            return calendar_error_response(
                "BAD_REQUEST",
                "Invalid year or month.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        start_dt = timezone.make_aware(datetime.combine(d, datetime.min.time()), timezone.utc)
        # next month
        if month == 12:
            next_month = date(year=year + 1, month=1, day=1)
        else:
            next_month = date(year=year, month=month + 1, day=1)
        end_dt = timezone.make_aware(datetime.combine(next_month, datetime.min.time()), timezone.utc)

        calendar_ids_param = request.query_params.get("calendar_ids")
        calendar_ids = calendar_ids_param.split(",") if calendar_ids_param else None
        project_id_param = request.query_params.get("project_id")
        project_id = resolve_project_pk(project_id_param)

        payload = _build_calendar_view_payload(
            request.user,
            start_dt,
            end_dt,
            calendar_ids,
            project_id,
            view_type="month",
        )
        if isinstance(payload, Response):
            return payload
        return Response(payload)


class AgendaView(generics.GenericAPIView):
    """
    Agenda view for an arbitrary datetime range.
    """

    permission_classes = [IsAuthenticatedInOrganization]

    def get(self, request, *args, **kwargs):
        start_raw = request.query_params.get("start_date")
        end_raw = request.query_params.get("end_date")
        if not start_raw:
            return calendar_error_response(
                "BAD_REQUEST",
                "start_date query parameter is required.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        try:
            start_dt = _parse_iso_datetime(start_raw)
            if end_raw:
                end_dt = _parse_iso_datetime(end_raw)
            else:
                end_dt = start_dt + timedelta(days=7)
        except ValueError as exc:
            return calendar_error_response(
                "INVALID_DATETIME",
                str(exc),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        calendar_ids_param = request.query_params.get("calendar_ids")
        calendar_ids = calendar_ids_param.split(",") if calendar_ids_param else None
        project_id_param = request.query_params.get("project_id")
        project_id = resolve_project_pk(project_id_param)

        payload = _build_calendar_view_payload(
            request.user,
            start_dt,
            end_dt,
            calendar_ids,
            project_id,
            view_type="agenda",
        )
        if isinstance(payload, Response):
            return payload

        # Apply optional limit
        limit_raw = request.query_params.get("limit")
        if limit_raw and isinstance(payload.get("events"), list):
            try:
                limit = max(1, min(int(limit_raw), 500))
                payload["events"] = payload["events"][:limit]
            except (TypeError, ValueError):
                pass

        return Response(payload)


class EventAttendeeListCreateView(generics.ListCreateAPIView):
    """
    List and add attendees for a specific event.
    """

    serializer_class = EventAttendeeSerializer
    permission_classes = [IsAuthenticatedInOrganization]

    def _get_event(self) -> Event:
        event_id = self.kwargs["event_id"]
        event = get_object_or_404(
            Event,
            id=event_id,
            is_deleted=False,
        )

        # Object-level permission via EventAccessPermission
        perm = EventAccessPermission()
        setattr(self, "required_permission", "view_all")
        if not perm.has_object_permission(self.request, self, event):
            raise PermissionDenied("You do not have access to this event.")

        return event

    def get_queryset(self):
        event = self._get_event()
        return (
            event.attendees.select_related("user")
            .filter(is_deleted=False)
            .order_by("-is_organizer", "attendee_type", "email")
        )

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        event = self._get_event()

        # Adding attendees requires edit permission
        perm = EventAccessPermission()
        setattr(self, "required_permission", "edit")
        if not perm.has_object_permission(self.request, self, event):
            raise PermissionDenied("You do not have permission to modify attendees.")

        req_serializer = AttendeeCreateRequestSerializer(data=request.data)
        req_serializer.is_valid(raise_exception=True)
        data = req_serializer.validated_data

        user = None
        email = data.get("email")

        if data.get("user_id"):
            user_model = self.request.user.__class__
            user_queryset = user_model.objects.filter(id=data["user_id"])
            if not event.calendar.project_id:
                user_queryset = user_queryset.filter(organization_id=event.organization_id)
            user = get_object_or_404(user_queryset)
            if not email:
                email = user.email

        attendee = EventAttendee(
            organization=event.organization,
            event=event,
            user=user,
            email=email,
            display_name=data.get("display_name") or "",
            attendee_type=data.get("attendee_type", "required"),
        )
        attendee.save()

        serializer = self.get_serializer(attendee)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class EventAttendeeDetailView(generics.DestroyAPIView):
    """
    Remove an attendee from an event.
    """

    permission_classes = [IsAuthenticatedInOrganization]
    serializer_class = EventAttendeeSerializer
    lookup_url_kwarg = "attendee_id"

    def _get_event(self) -> Event:
        event_id = self.kwargs["event_id"]
        event = get_object_or_404(
            Event,
            id=event_id,
            is_deleted=False,
        )

        perm = EventAccessPermission()
        setattr(self, "required_permission", "edit")
        if not perm.has_object_permission(self.request, self, event):
            raise PermissionDenied("You do not have permission to modify attendees.")

        return event

    def get_object(self):
        event = self._get_event()
        attendee_id = self.kwargs["attendee_id"]
        attendee = get_object_or_404(
            EventAttendee,
            id=attendee_id,
            organization=event.organization,
            event=event,
            is_deleted=False,
        )
        return attendee

    def perform_destroy(self, instance: EventAttendee):
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted", "updated_at"])


class EventRSVPView(generics.GenericAPIView):
    """
    RSVP endpoint for the authenticated user.
    """

    serializer_class = AttendeeResponseRequestSerializer
    permission_classes = [IsAuthenticatedInOrganization]

    def _get_event(self) -> Event:
        event_id = self.kwargs["event_id"]
        event = get_object_or_404(
            Event,
            id=event_id,
            is_deleted=False,
        )

        perm = EventAccessPermission()
        setattr(self, "required_permission", "view_all")
        if not perm.has_object_permission(self.request, self, event):
            raise PermissionDenied("You do not have access to this event.")

        return event

    def post(self, request, *args, **kwargs):
        event = self._get_event()

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = request.user

        attendee = (
            EventAttendee.objects.filter(
                organization=event.organization,
                event=event,
                user=user,
                is_deleted=False,
            )
            .select_related("user")
            .first()
        )

        if not attendee:
            attendee = EventAttendee(
                organization=event.organization,
                event=event,
                user=user,
                email=getattr(user, "email", None),
                attendee_type="required",
            )

        attendee.response_status = data["response_status"]
        attendee.response_comment = data.get("response_comment") or ""
        attendee.save()

        output = EventAttendeeSerializer(attendee)
        return Response(output.data, status=status.HTTP_200_OK)


class EventInstanceModifyView(generics.GenericAPIView):
    """
    Modify a specific instance of a recurring event.
    """

    serializer_class = EventCreateUpdateSerializer
    permission_classes = [IsAuthenticatedInOrganization, EventAccessPermission]

    def _get_event(self, request, *args, **kwargs) -> Event:
        event_id = self.kwargs["event_id"]
        event = get_object_or_404(
            Event,
            id=event_id,
            is_deleted=False,
        )

        perm = EventAccessPermission()
        setattr(self, "required_permission", "edit")
        if not perm.has_object_permission(request, self, event):
            raise PermissionDenied("You do not have permission to modify this event.")

        if not event.is_recurring or not event.recurrence_rule_id:
            raise PermissionDenied("Event is not recurring.")

        return event

    def patch(self, request, *args, **kwargs):
        event = self._get_event(request, *args, **kwargs)

        original_start_raw = request.query_params.get("original_start")
        if not original_start_raw:
            return Response(
                {"detail": "original_start query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            original_start = _parse_iso_datetime(original_start_raw)
        except ValueError:
            return Response(
                {"detail": "Invalid datetime format for original_start."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            modified_event = modify_single_occurrence(
                event,
                original_start,
                request.data,
                context={"request": request},
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output = EventSerializer(modified_event)
        return Response(output.data, status=status.HTTP_200_OK)


class EventInstanceCancelView(generics.GenericAPIView):
    """
    Cancel (delete) a specific instance of a recurring event.
    """

    permission_classes = [IsAuthenticatedInOrganization, EventAccessPermission]

    def _get_event(self, request, *args, **kwargs) -> Event:
        event_id = self.kwargs["event_id"]
        event = get_object_or_404(
            Event,
            id=event_id,
            is_deleted=False,
        )

        perm = EventAccessPermission()
        setattr(self, "required_permission", "edit")
        if not perm.has_object_permission(request, self, event):
            raise PermissionDenied("You do not have permission to modify this event.")

        if not event.is_recurring or not event.recurrence_rule_id:
            raise PermissionDenied("Event is not recurring.")

        return event

    def delete(self, request, *args, **kwargs):
        event = self._get_event(request, *args, **kwargs)

        original_start_raw = request.query_params.get("original_start")
        if not original_start_raw:
            return Response(
                {"detail": "original_start query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            original_start = _parse_iso_datetime(original_start_raw)
        except ValueError:
            return Response(
                {"detail": "Invalid datetime format for original_start."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            cancel_single_occurrence(event, original_start)
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)


class EventInstanceModifyFutureView(generics.GenericAPIView):
    """
    Modify a recurring event from a given occurrence onward ("this and future").

    Splits the series: the master is capped just before the selected occurrence
    and a new recurring event/series is created from that occurrence with the
    edited values. Returns the new series master event.
    """

    serializer_class = EventCreateUpdateSerializer
    permission_classes = [IsAuthenticatedInOrganization, EventAccessPermission]

    def _get_event(self, request, *args, **kwargs) -> Event:
        event_id = self.kwargs["event_id"]
        event = get_object_or_404(
            Event,
            id=event_id,
            is_deleted=False,
        )

        perm = EventAccessPermission()
        setattr(self, "required_permission", "edit")
        if not perm.has_object_permission(request, self, event):
            raise PermissionDenied("You do not have permission to modify this event.")

        if not event.is_recurring or not event.recurrence_rule_id:
            raise PermissionDenied("Event is not recurring.")

        return event

    def post(self, request, *args, **kwargs):
        event = self._get_event(request, *args, **kwargs)

        original_start_raw = request.query_params.get("original_start")
        if not original_start_raw:
            return Response(
                {"detail": "original_start query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            original_start = _parse_iso_datetime(original_start_raw)
        except ValueError:
            return Response(
                {"detail": "Invalid datetime format for original_start."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            new_event = split_series_from_occurrence(
                event,
                original_start,
                request.data,
                context={"request": request},
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output = EventSerializer(new_event)
        return Response(output.data, status=status.HTTP_201_CREATED)


class FreeBusyView(generics.GenericAPIView):
    """
    Free/busy info for calendars within a time range.
    """

    permission_classes = [IsAuthenticatedInOrganization]

    def post(self, request, *args, **kwargs):
        body = request.data or {}

        time_min_raw = body.get("time_min")
        time_max_raw = body.get("time_max")
        if not time_min_raw or not time_max_raw:
            return calendar_error_response(
                "BAD_REQUEST",
                "time_min and time_max are required.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        try:
            time_min = _parse_iso_datetime(time_min_raw)
            time_max = _parse_iso_datetime(time_max_raw)
        except ValueError as exc:
            return calendar_error_response(
                "INVALID_DATETIME",
                str(exc),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if time_min >= time_max:
            return calendar_error_response(
                "BAD_REQUEST",
                "time_min must be earlier than time_max.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        calendar_ids = body.get("calendar_ids") or []
        if isinstance(calendar_ids, str):
            calendar_ids = [calendar_ids]
        project_id_raw = body.get("project_id")
        project_id = resolve_project_pk(project_id_raw)

        calendars = _get_accessible_calendars(request.user, calendar_ids or None, project_id=project_id)

        result = {
            "time_min": time_min.isoformat().replace("+00:00", "Z"),
            "time_max": time_max.isoformat().replace("+00:00", "Z"),
            "calendars": {},
        }

        # Expansion + merge live in services so booking availability reuses the
        # same computation rather than a second copy of it.
        by_calendar = get_busy_intervals_by_calendar(calendars, time_min, time_max)

        for calendar_id, intervals in by_calendar.items():
            result["calendars"][calendar_id] = {
                "busy": [
                    {
                        "start": s.isoformat().replace("+00:00", "Z"),
                        "end": e.isoformat().replace("+00:00", "Z"),
                    }
                    for s, e in intervals
                ],
                "errors": [],
            }

        return Response(result, status=status.HTTP_200_OK)


class EventReminderListCreateView(generics.ListCreateAPIView):
    """
    List and add reminders for a specific event.
    """

    serializer_class = EventReminderSerializer
    permission_classes = [IsAuthenticatedInOrganization]

    def _get_event(self) -> Event:
        event_id = self.kwargs["event_id"]
        event = get_object_or_404(
            Event,
            id=event_id,
            is_deleted=False,
        )

        perm = EventAccessPermission()
        # Listing reminders requires view_all
        setattr(self, "required_permission", "view_all")
        if not perm.has_object_permission(self.request, self, event):
            raise PermissionDenied("You do not have access to this event.")

        return event

    def get_queryset(self):
        event = self._get_event()
        return event.reminders.select_related("user").filter(is_deleted=False).order_by("scheduled_time")

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        event = self._get_event()

        # Creating reminders requires edit permission
        perm = EventAccessPermission()
        setattr(self, "required_permission", "edit")
        if not perm.has_object_permission(self.request, self, event):
            raise PermissionDenied("You do not have permission to modify reminders.")

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        reminder = serializer.save(
            organization=event.organization,
            event=event,
            user=request.user if request.user.is_authenticated else None,
        )

        output = self.get_serializer(reminder)
        return Response(output.data, status=status.HTTP_201_CREATED)

# SMP-407
from .models import CalendarEvent
from .serializers import CalendarEventSerializer

class CalendarEventListView(generics.ListAPIView):
    """
    Read-only endpoint for system-derived calendar events.
    Events are auto-generated from Decisions and Tasks.
    Supports filtering by date range, event type, owner, and project.
    No mutation is allowed — this API is GET only.
    """

    serializer_class = CalendarEventSerializer
    permission_classes = [IsAuthenticatedInOrganization]

    def get_queryset(self):
        user = self.request.user
        organization = get_user_organization(user)
        if not organization:
            return CalendarEvent.objects.none()

        return get_calendar_events(
            organization=organization,
            start=self.request.query_params.get('start'),
            end=self.request.query_params.get('end'),
            event_type=self.request.query_params.get('event_type'),
            project_id=resolve_project_pk(self.request.query_params.get('project_id')),
        )

# ── Public booking links ────────────────────────────────────────
#
# These are the only unauthenticated endpoints in this app. Two consequences
# shape everything below:
#
# 1. TenantSchemaMiddleware resolves the schema from the authenticated user and
#    falls back to `public` when there is none. Booking links are tenant-scoped,
#    so each view must resolve the organisation from the URL and switch schema
#    itself — see core.services.tenant.tenant_schema.
# 2. Anything read here is readable by anyone holding the URL, and anything
#    written here is written by an anonymous caller. Responses are therefore
#    kept narrow, and both views are throttled by IP.


# How far ahead an availability query may look in one request, independent of
# the link's own horizon. Keeps a single request from expanding months of slots.
MAX_AVAILABILITY_WINDOW_DAYS = 62
DEFAULT_AVAILABILITY_WINDOW_DAYS = 14


def _resolve_booking_org(org_slug: str):
    """
    Resolve the URL's org slug to an Organization, or None.

    Runs before any schema switch, because Organization lives in the public
    schema. Validating here is what makes the slug safe to hand to
    tenant_schema(); an unvalidated slug would let a caller aim queries at any
    schema name they can guess.
    """
    return Organization.objects.filter(slug=org_slug, is_active=True).first()


def _load_active_booking_link(link_slug: str, organization):
    """Fetch a live booking link. Must be called inside the tenant schema."""
    return (
        BookingLink.objects.filter(
            organization=organization,
            slug=link_slug,
            is_active=True,
            is_deleted=False,
            owner__is_active=True,
            calendar__is_deleted=False,
        )
        .select_related("owner", "calendar")
        .prefetch_related("invitee_users")
        .first()
    )


def _booking_from_token(event_id, organization, link_slug):
    event = Event.objects.filter(id=event_id, organization=organization).first()
    if not event:
        return None, None
    meta = event.metadata or {}
    links = BookingLink.objects.filter(organization=organization)
    if meta.get("booking_link_id"):
        link = links.filter(pk=meta["booking_link_id"]).first()
        if not link or link_slug not in {link.slug, meta.get("booking_link_slug")}:
            return None, None
    else:
        link = links.filter(slug=link_slug).order_by("-created_at").first()
    if not link or not event_belongs_to_booking_link(event, link):
        return None, None
    return link, event


def _booking_not_found():
    """
    One indistinguishable response for every miss.

    A wrong org, a wrong link, an inactive link and a deleted link all answer
    identically, so the endpoint cannot be used to enumerate which
    organisations or links exist.
    """
    return calendar_error_response(
        "NOT_FOUND",
        "This booking link is not available.",
        status_code=status.HTTP_404_NOT_FOUND,
    )


def _google_connection_for(user_id):
    """
    The owner's Google connection, if any.

    GoogleCalendarConnection is not tenant-scoped — it lives in the public
    schema — so this resolves correctly regardless of the active search_path.
    """
    from google_calendar_integration.models import GoogleCalendarConnection

    return GoogleCalendarConnection.objects.filter(user_id=user_id).first()


def _google_busy_for_link(link):
    """Personal links consult the host's Google diary; team links do not."""
    if is_team_booking_calendar(getattr(link, "calendar", None)):
        return None
    return _google_connection_for(link.owner_id)


class PublicBookingLinkAvailabilityView(APIView):
    """
    GET /api/public/book/<org_slug>/<link_slug>/

    Link details plus bookable slots. Anonymous visitors can read an open
    link. An invitees-only link answers 404 until a named person is signed
    in, same as a missing link — otherwise the payload would confirm the
    link exists and who it is for.
    """

    permission_classes = [AllowAny]
    authentication_classes = [TenantAwareJWTAuthentication]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_booking_read"

    def get(self, request, org_slug: str, link_slug: str):
        organization = _resolve_booking_org(org_slug)
        if not organization:
            return _booking_not_found()

        try:
            range_start, range_end = self._parse_range(request)
        except (ValueError, OverflowError) as exc:
            return calendar_error_response(
                "BAD_REQUEST", "Invalid availability range.", status_code=status.HTTP_400_BAD_REQUEST
            )

        with tenant_schema_context(slug_to_schema_name(organization.slug)):
            link = _load_active_booking_link(link_slug, organization)
            if not link:
                return _booking_not_found()

            if not can_book_public_link(link, request.user):
                return _booking_not_found()

            payload = PublicBookingLinkSerializer(link).data
            payload["viewer_can_book"] = True
            payload["same_project"] = booker_shares_project(link, request.user)
            payload["viewer_bookings"] = serialize_viewer_bookings(
                find_viewer_bookings(link, request.user)
            )

            # Windows and their timezone must come from the same source — see
            # AvailabilitySchedule.
            schedule = schedule_from_booking_link(link)
            slots = get_merged_availability(
                calendars=calendars_for_booking_availability(link),
                google_connection=_google_busy_for_link(link),
                rules=rules_from_booking_link(link),
                windows=schedule.windows,
                tz_name=schedule.timezone,
                range_start=range_start,
                range_end=range_end,
            )

        payload["slots"] = [
            {
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
            }
            for start, end in slots
        ]
        return Response(payload, status=status.HTTP_200_OK)

    def _parse_range(self, request):
        now = timezone.now()

        start_raw = request.query_params.get("from")
        end_raw = request.query_params.get("to")

        range_start = _parse_iso_datetime(start_raw) if start_raw else now
        if end_raw:
            range_end = _parse_iso_datetime(end_raw)
        else:
            range_end = range_start + timedelta(days=DEFAULT_AVAILABILITY_WINDOW_DAYS)

        if range_end <= range_start:
            raise ValueError("'to' must be later than 'from'.")
        if range_end - range_start > timedelta(days=MAX_AVAILABILITY_WINDOW_DAYS):
            raise ValueError(
                f"Range cannot exceed {MAX_AVAILABILITY_WINDOW_DAYS} days."
            )
        # Never offer slots in the past, however wide the requested window.
        return max(range_start, now), range_end


class PublicBookingCreateView(APIView):
    """
    POST /api/public/book/<org_slug>/<link_slug>/bookings/

    Create a booking. Guests may book anonymously; a signed-in member's
    name and email come from the account. Tightly throttled: each call
    writes a real calendar event.
    """

    permission_classes = [AllowAny]
    authentication_classes = [TenantAwareJWTAuthentication]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_booking_write"

    def post(self, request, org_slug: str, link_slug: str):
        organization = _resolve_booking_org(org_slug)
        if not organization:
            return _booking_not_found()

        serializer = BookingRequestSerializer(
            data=request.data,
            context={"user": request.user},
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with tenant_schema_context(slug_to_schema_name(organization.slug)), transaction.atomic():
            link = _load_active_booking_link(link_slug, organization)
            if not link:
                return _booking_not_found()

            if not can_book_public_link(link, request.user):
                return _booking_not_found()

            # Lock every diary we will write, in a stable order. Reciprocal
            # member bookings otherwise lock A then B / B then A and deadlock.
            # NO KEY UPDATE also lets unrelated foreign-key inserts proceed.
            initial_owner_id = link.owner_id
            calendar_ids = {link.calendar_id}
            guest = self._guest_account(link, data["email"])
            booker = request.user if getattr(request.user, "is_authenticated", False) else None
            existing = find_upcoming_guest_booking(link, booker) if booker else None
            if existing:
                calendar_ids.add(existing.calendar_id)
            if guest and guest.pk != link.owner_id and guest.organization_id == link.organization_id:
                guest_calendar = ensure_personal_calendar(
                    organization=link.organization, owner=guest, timezone=link.timezone,
                )
                calendar_ids.add(guest_calendar.pk)
            locked = list(Calendar.objects.select_for_update(no_key=True).filter(
                pk__in=calendar_ids, is_deleted=False,
            ).order_by("pk"))
            calendar = next((item for item in locked if item.pk == link.calendar_id), None)
            if calendar is None:
                return _booking_not_found()
            # Re-read permissions and rules after waiting; the host may have
            # disabled or changed this link while another booking held the lock.
            link = BookingLink.objects.select_for_update().get(pk=link.pk)
            if (link.is_deleted or not link.is_active or link.owner_id != initial_owner_id
                    or not link.owner.is_active or link.calendar_id != calendar.pk
                    or not can_book_public_link(link, request.user)):
                return _booking_not_found()
            link.calendar = calendar
            if data["email"].strip().lower() == (link.owner.email or "").strip().lower():
                return calendar_error_response("BAD_REQUEST", "The host cannot book their own link.", status_code=400)

            rules = rules_from_booking_link(link)
            schedule = schedule_from_booking_link(link)
            google_connection = _google_busy_for_link(link)

            booker = request.user if getattr(request.user, "is_authenticated", False) else None
            existing = find_upcoming_guest_booking(link, booker) if booker else None
            if existing and existing.start_datetime == data["start"]:
                return calendar_error_response(
                    "SLOT_UNAVAILABLE", "You already booked this time.",
                    status_code=status.HTTP_409_CONFLICT,
                )
            replaced = cancel_booking_events(existing) if existing else []

            # Availability was rendered from a snapshot, so the slot may have
            # been taken while the page sat open. Without this re-check the same
            # slot can be booked twice.
            try:
                slot_available = is_slot_still_available(
                    calendars=calendars_for_booking_availability(link),
                    google_connection=google_connection,
                    rules=rules,
                    windows=schedule.windows,
                    tz_name=schedule.timezone,
                    slot_start=data["start"],
                )
            except GoogleAvailabilityUnavailable:
                transaction.set_rollback(True)
                return calendar_error_response(
                    "AVAILABILITY_UNAVAILABLE",
                    "We couldn't verify this time. Please try again shortly.",
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            if not slot_available:
                transaction.set_rollback(True)
                return calendar_error_response(
                    "SLOT_UNAVAILABLE",
                    "That time is no longer available. Please pick another slot.",
                    status_code=status.HTTP_409_CONFLICT,
                )

            event, guest = self._create_event(link, data, rules)
            link_timezone = schedule.timezone
            if existing:
                notify_booking_rescheduled(link, event, guest, data["name"])
            else:
                notify_booking_made(link, event, guest, data["name"])
            if guest:
                mark_invite_booked(link, guest, event, make_cancel_token(event.pk))
            for old_copy in replaced:
                queue_booking_task(export_event_to_google_task, str(old_copy.pk),
                                   tenant_schema=slug_to_schema_name(organization.slug))

        # Export asynchronously after commit, as every other event write in this
        # module does. The local event is the source of truth: a Google outage
        # must not lose a confirmed booking, and the prospect should not wait on
        # an external API. The worker needs the schema explicitly — it never
        # passes through TenantSchemaMiddleware.
        event_id = str(event.pk)
        schema = slug_to_schema_name(organization.slug)
        if not is_team_booking_calendar(getattr(link, "calendar", None)):
            queue_booking_task(export_event_to_google_task, event_id, tenant_schema=schema)

        token = make_cancel_token(event.pk)
        cancel_url = request.build_absolute_uri(
            f"/book/{quote(org_slug)}/{quote(link_slug)}/cancel?token={quote(token)}"
        )
        # Token in the path, not ?token=: Outlook desktop strips query
        # strings when adding an internet calendar, then the feed 404s.
        # Colon in a signed token also has to be encoded — reverse()
        # leaves it raw, and some clients treat it as a delimiter.
        placeholder = "FEEDTOKEN"
        feed_path = reverse(
            "public-booking-feed",
            kwargs={
                "org_slug": org_slug,
                "link_slug": link_slug,
                "token": placeholder,
            },
        ).replace(placeholder, quote(make_feed_token(event.pk), safe=""))
        feed_url = as_webcal_url(request.build_absolute_uri(feed_path))
        self._queue_confirmation_email(
            event=event,
            link=link,
            data=data,
            cancel_url=cancel_url,
            feed_url=feed_url,
        )

        return Response(
            {
                "status": "confirmed",
                "start": event.start_datetime.isoformat().replace("+00:00", "Z"),
                "end": event.end_datetime.isoformat().replace("+00:00", "Z"),
                "title": event.title,
                "timezone": link_timezone,
                # The guest's handle on this booking. Also emailed, so closing
                # the tab is no longer the end of it.
                "cancel_token": token,
                # Subscribing keeps their calendar in step with ours - the only
                # way a guest with no account hears about a cancellation.
                "feed_url": feed_url,
            },
            status=status.HTTP_201_CREATED,
        )

    def _queue_confirmation_email(self, *, event, link, data, cancel_url, feed_url):
        """
        Email is the guest's only channel: no account, so no notifications.

        Fired after commit so a booking is never emailed before it is durable,
        and never blocks the response.
        """
        ics_body = build_booking_ics(
            uid=f"{event.pk}@marketing-simplified",
            title=event.title,
            start=event.start_datetime,
            end=event.end_datetime,
            description=event.description or "",
            url=feed_url,
            organizer_email=link.owner.email or "",
        )
        payload = {
            "to_email": data["email"],
            "guest_name": data["name"],
            "host_name": link.owner.get_full_name() or link.owner.get_username(),
            "title": event.title,
            "when": event.start_datetime.strftime("%A %d %B %Y, %H:%M UTC"),
            "ics_body": ics_body,
            "cancel_url": cancel_url,
            "feed_url": feed_url,
        }
        queue_booking_task(send_booking_confirmation_task, **payload)

    @staticmethod
    def _event_description(data) -> str:
        """
        Put the guest's contact details where the host will actually see them.

        They are also stored properly on the EventAttendee row, but nothing in
        the calendar UI renders attendees today - so a phone number recorded
        only there would be collected and never read. The description is the
        one field the event dialog shows, which makes it the honest place for
        this until an attendee panel exists.
        """
        lines = [f"Booked by {data['name']}", data["email"]]
        if data.get("phone"):
            lines.append(data["phone"])
        notes = (data.get("notes") or "").strip()
        if notes:
            lines += ["", notes]
        return "\n".join(lines)

    @transaction.atomic
    def _create_event(self, link, data, rules):
        start = data["start"]
        end = start + timedelta(minutes=rules.duration_minutes)
        guest = self._guest_account(link, data["email"])
        # The selected calendar receives the host event. A verified account
        # also receives a personal copy; an anonymous email proves no identity.
        event, _guest = create_booking_events(
            link=link,
            title=f"{link.title} with {data['name']}"[:255],
            description=self._event_description(data),
            start=start,
            end=end,
            guest_user=guest,
            guest_name=data["name"],
            guest_email=data["email"],
            guest_phone=data.get("phone", ""),
        )
        return event, guest

    def _guest_account(self, link, email: str):
        user = self.request.user
        if not getattr(user, "is_authenticated", False):
            return None
        if user.organization_id == link.organization_id:
            return user
        if is_team_booking_calendar(link.calendar) and link.invitee_users.filter(pk=user.pk).exists():
            return user
        return None


class PublicBookingFeedView(APIView):
    """
    GET /api/public/book/<org_slug>/<link_slug>/<token>.ics
    GET /api/public/book/<org_slug>/<link_slug>/calendar.ics?token=...

    The guest's booking as a subscribable calendar feed.

    Distinct from the .ics the browser builds at confirmation time, which is a
    snapshot: once saved, it never learns that the host cancelled. A calendar
    app subscribed to this URL re-fetches it, so a cancellation reaches the
    guest even though they have no account and no notification of any kind.

    Read-only and idempotent, so unlike the cancel endpoint it is safe for the
    prefetching that mail and chat clients do.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_booking_read"

    def perform_content_negotiation(self, request, force=False):
        # Successful responses are already rendered as text/calendar below.
        # Calendar clients request that MIME type, which DRF's JSON renderers
        # otherwise reject with 406 before get() runs. Keep JSON for errors.
        return super().perform_content_negotiation(request, force=True)

    def get(self, request, org_slug: str, link_slug: str, token: str | None = None):
        organization = _resolve_booking_org(org_slug)
        if not organization:
            return _booking_not_found()

        event_id = read_feed_token(token or request.query_params.get("token") or "")
        if not event_id:
            return _booking_not_found()

        with tenant_schema_context(slug_to_schema_name(organization.slug)):
            link, event = _booking_from_token(event_id, organization, link_slug)
            if not link or not event or not event_belongs_to_booking_link(event, link):
                return _booking_not_found()

            cancelled = bool(event.is_deleted) or event.status == "cancelled"
            body = build_booking_ics(
                uid=f"{event.pk}@marketing-simplified",
                title=event.title,
                start=event.start_datetime,
                end=event.end_datetime,
                description=event.description or "",
                url=request.build_absolute_uri(),
                organizer_email=link.owner.email or "",
                cancelled=cancelled,
            )

        response = HttpResponse(body, content_type="text/calendar; charset=utf-8")
        response["Content-Disposition"] = 'inline; filename="booking.ics"'
        # The whole point is that subscribers see changes; a cached copy would
        # keep showing a meeting that has been called off.
        response["Cache-Control"] = "no-store, max-age=0"
        return response


class PublicBookingCancelView(APIView):
    """
    POST /api/public/book/<org_slug>/<link_slug>/cancel/

    Let a guest call off a booking they made. The token is the whole of the
    authorisation: it proves they hold something only issued to them at booking
    time, which is as much as can be asked of someone with no account.

    Cancelling from either side ends the meeting for both, matching how Google
    and Outlook behave - a half-cancelled meeting is worse than none.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_booking_write"

    def post(self, request, org_slug: str, link_slug: str):
        organization = _resolve_booking_org(org_slug)
        if not organization:
            return _booking_not_found()

        event_id = read_cancel_token((request.data or {}).get("token") or "")
        if not event_id:
            # Same answer as an unknown link: a bad token must not confirm that
            # some other booking exists.
            return _booking_not_found()

        with tenant_schema_context(slug_to_schema_name(organization.slug)), transaction.atomic():
            link, event = _booking_from_token(event_id, organization, link_slug)
            # The token names an event; the URL has to name the same booking, or
            # a token could be replayed against an unrelated link.
            if not link or not event or not event_belongs_to_booking_link(event, link):
                return _booking_not_found()

            event = lock_booking_event(event)
            already_cancelled = bool(event.is_deleted) or event.status == "cancelled"
            guest = (
                EventAttendee.objects.filter(event=event, is_organizer=False)
                .exclude(user=None)
                .first()
            )
            siblings = cancel_booking_events(event)

            if not already_cancelled:
                notify_booking_cancelled(
                    event,
                    host_id=link.owner_id,
                    guest_user_id=guest.user_id if guest else None,
                    actor=None,
                    by_guest=True,
                )
                if guest and guest.user_id:
                    mark_invite_unbooked(link, guest.user)

        # Soft-deleting is what makes the export remove the Google copy.
        # Queue every sibling: only the primary calendar actually exports.
        schema = slug_to_schema_name(organization.slug)
        export_ids = {str(event.pk)} | {str(sibling.pk) for sibling in siblings}
        for event_pk in export_ids:
            queue_booking_task(export_event_to_google_task, event_pk, tenant_schema=schema)
        return Response({"status": "cancelled"}, status=status.HTTP_200_OK)


class PublicBookingLookupView(APIView):
    """Request recovery by email without disclosing bookings or bearer tokens."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_booking_write"

    def post(self, request, org_slug: str, link_slug: str):
        serializer = BookingLookupSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            send_booking_recovery_task.delay(
                org_slug=org_slug, link_slug=link_slug,
                email=serializer.validated_data["email"],
                base_url=request.build_absolute_uri("/").rstrip("/"),
            )
        except Exception:
            return calendar_error_response(
                "RECOVERY_UNAVAILABLE", "Recovery is temporarily unavailable. Please try again later.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"status": "accepted"}, status=status.HTTP_202_ACCEPTED)


class BookingLinkViewSet(viewsets.ModelViewSet):
    """
    Owner-facing CRUD for booking links.

    Scoped to links you are party to - ones you host, and ones you set up for a
    colleague. A link you are unconnected to is neither listable nor
    addressable, even inside the same organisation, so update and delete
    inherit that boundary from the queryset.

    `organization` is set from the request. The host comes from the serializer,
    which checks the requester shares a project with them, and that they are a
    project owner/admin if the host is someone else.

    The public counterparts (PublicBookingLinkAvailabilityView /
    PublicBookingCreateView) are the anonymous read + book side of the same
    model, and deliberately expose far less.
    """

    serializer_class = BookingLinkSerializer
    permission_classes = [IsAuthenticatedInOrganization]
    # An owner's links are a naturally small set and the management UI wants
    # them all; the global PAGE_SIZE would silently truncate the list.
    pagination_class = None

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        Organization.objects.select_for_update(no_key=True).get(pk=request.user.organization_id)
        return super().create(request, *args, **kwargs)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        BookingLink.objects.select_for_update().get(pk=self.get_object().pk)
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        BookingLink.objects.select_for_update().get(pk=self.get_object().pk)
        return super().destroy(request, *args, **kwargs)

    def get_serializer_context(self):
        # Reuse the app's single definition of "calendars this user can use"
        # rather than re-deriving it in the serializer.
        context = super().get_serializer_context()
        context["accessible_calendars"] = _get_accessible_calendars(self.request.user)
        return context

    def get_queryset(self):
        organization = get_user_organization(self.request.user)
        if not organization:
            return BookingLink.objects.none()
        return (
            BookingLink.objects.filter(
                Q(owner=self.request.user) | Q(created_by=self.request.user),
                organization=organization,
                is_deleted=False,
            )
            .select_related("calendar", "owner", "created_by")
            .prefetch_related("invitee_users")
            .distinct()
            .order_by("-created_at", "title")
        )

    def perform_create(self, serializer):
        organization = get_user_organization(self.request.user)
        if not organization:
            raise PermissionDenied("An organization is required to create booking links.")

        extra = {"organization": organization, "created_by": self.request.user}
        # Default the timezone from the host's calendar settings rather than
        # asking for it. A per-link zone is an override, not something every
        # user should have to answer — and a wrong answer silently shifts every
        # offered slot. It must be the host's zone, not the creator's: the
        # windows describe the host's working day.
        if not serializer.validated_data.get("timezone"):
            host = serializer.validated_data.get("owner", self.request.user)
            settings_obj = CalendarSettings.objects.filter(user=host).first()
            extra["timezone"] = (settings_obj and settings_obj.timezone) or "UTC"

        link = serializer.save(**extra)
        notify_link_created(link, self.request.user)

    def perform_update(self, serializer):
        # Only the people newly attached need telling. Re-announcing the link on
        # every rules tweak would train everyone to ignore the bell.
        before = self.get_object()
        previous = {before.owner_id} | set(
            before.invitee_users.values_list("pk", flat=True)
        )
        link = serializer.save()
        current = {link.owner_id} | set(link.invitee_users.values_list("pk", flat=True))
        newly_added = current - previous
        if newly_added:
            notify_link_created(link, self.request.user, only_ids=newly_added)

    def perform_destroy(self, instance):
        # Soft delete, matching the rest of this app. The partial unique
        # constraint excludes deleted rows, so the slug becomes reusable.
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted", "updated_at"], validate=False)

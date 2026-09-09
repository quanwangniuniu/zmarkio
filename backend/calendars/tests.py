from datetime import datetime, timedelta, timezone as dt_timezone

from django.contrib.auth import get_user_model, models as auth_models
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import Organization, Project, ProjectMember
from calendars.models import (
    Calendar,
    CalendarShare,
    CalendarSubscription,
    Event,
    RecurrenceRule,
    RecurrenceException,
    EventAttendee,
    EventReminder,
    EventCategory,
    EventCategoryAssignment,
    CalendarSettings,
    Notification,
)
from calendars.views import (
    CalendarViewSet,
    SubscriptionListCreateView,
    SubscriptionDetailView,
    CalendarShareListCreateView,
    CalendarShareDetailView,
    EventViewSet,
    EventSearchView,
    EventAttendeeListCreateView,
    EventAttendeeDetailView,
    EventRSVPView,
    EventInstancesView,
    EventInstanceModifyView,
    EventInstanceCancelView,
    DayView,
    WeekView,
    MonthView,
    AgendaView,
    FreeBusyView,
    EventReminderListCreateView,
)
from calendars.exceptions import (
    calendar_error_response,
    _build_details_from_errors,
    calendar_exception_handler,
)
from calendars.permissions import (
    IsAuthenticatedInOrganization,
    CalendarAccessPermission,
    EventAccessPermission,
    SubscriptionOwnerPermission,
)


User = get_user_model()


class CalendarTestBase(TestCase):
    """
    Common setup helpers for calendar tests.
    """

    def setUp(self):
        super().setUp()
        self.factory = APIRequestFactory()

        self.organization = Organization.objects.create(name="Org", slug="org")
        self.user = User.objects.create_user(
            email="user@example.com",
            password="test1234",
            username="user",
            organization=self.organization,
        )
        self.other_user = User.objects.create_user(
            email="other@example.com",
            password="test1234",
            username="other",
            organization=self.organization,
        )
        self.other_organization = Organization.objects.create(name="Other Org", slug="other-org")
        self.cross_org_user = User.objects.create_user(
            email="cross@example.com",
            password="test1234",
            username="cross",
            organization=self.other_organization,
        )

        self.calendar = Calendar.objects.create(
            organization=self.organization,
            owner=self.user,
            name="My Calendar",
            timezone="UTC",
            is_primary=True,
        )
        self.other_calendar = Calendar.objects.create(
            organization=self.organization,
            owner=self.other_user,
            name="Other Calendar",
            timezone="UTC",
            is_primary=True,
        )


class CalendarModelTests(CalendarTestBase):
    def test_calendar_organization_must_match_owner(self):
        other_org = Organization.objects.create(name="Other", slug="other")
        cal = Calendar(
            organization=other_org,
            owner=self.user,
            name="Invalid",
        )
        with self.assertRaises(ValidationError) as ctx:
            cal.full_clean()
        self.assertIn("Calendar.organization must match owner.organization", str(ctx.exception))

    def test_project_calendar_allows_cross_org_owner(self):
        other_org = Organization.objects.create(name="Other", slug="other-cross")
        cross_org_user = User.objects.create_user(
            username="crossowner",
            email="crossowner@other.com",
            password="pass12345",
            organization=other_org,
        )
        project = Project.objects.create(
            name="Cross Org Owner Project",
            organization=self.organization,
            owner=cross_org_user,
            objectives=["awareness"],
            kpis={"ctr": {"target": 0.02}},
        )
        cal = Calendar(
            organization=self.organization,
            owner=cross_org_user,
            project=project,
            name=f"{project.name} Calendar",
            timezone="UTC",
        )
        cal.full_clean()

    def test_only_one_primary_calendar_per_owner_and_org(self):
        initial_calendar = self.calendar
        self.assertTrue(initial_calendar.is_primary)
        new_calendar = Calendar.objects.create(
            organization=self.organization,
            owner=self.user,
            name="Secondary",
            timezone="UTC",
            is_primary=True,
        )
        self.assertTrue(new_calendar.is_primary)
        initial_calendar.refresh_from_db()
        self.assertFalse(initial_calendar.is_primary)
        primary_count = Calendar.objects.filter(
            organization=self.organization,
            owner=self.user,
            is_primary=True,
            is_deleted=False,
        ).count()
        self.assertEqual(primary_count, 1)


class CalendarSubscriptionTests(CalendarTestBase):
    def test_subscription_xor_calendar_and_source_url(self):
        sub = CalendarSubscription(
            organization=self.organization,
            user=self.user,
            calendar=self.calendar,
        )
        sub.full_clean()

        sub2 = CalendarSubscription(
            organization=self.organization,
            user=self.user,
            calendar=self.calendar,
            source_url="https://example.com/ical.ics",
        )
        with self.assertRaises(ValidationError):
            sub2.full_clean()


class EventModelTests(CalendarTestBase):
    def _create_basic_event(self, **kwargs) -> Event:
        start = timezone.now()
        end = start + timedelta(hours=1)
        data = {
            "organization": self.organization,
            "calendar": self.calendar,
            "created_by": self.user,
            "title": "Planning",
            "start_datetime": start,
            "end_datetime": end,
            "timezone": "UTC",
        }
        data.update(kwargs)
        event = Event(**data)
        event.save()
        return event

    def test_event_end_must_be_after_start(self):
        start = timezone.now()
        end = start - timedelta(minutes=10)
        event = Event(
            organization=self.organization,
            calendar=self.calendar,
            created_by=self.user,
            title="Invalid",
            start_datetime=start,
            end_datetime=end,
            timezone="UTC",
        )
        with self.assertRaises(ValidationError):
            event.full_clean()

    def test_event_recurring_requires_recurrence_rule(self):
        start = timezone.now()
        end = start + timedelta(hours=1)
        rule = RecurrenceRule.objects.create(
            organization=self.organization,
            frequency="DAILY",
            interval=1,
        )
        event = Event(
            organization=self.organization,
            calendar=self.calendar,
            created_by=self.user,
            title="Recurring",
            start_datetime=start,
            end_datetime=end,
            timezone="UTC",
            is_recurring=True,
            recurrence_rule=rule,
        )
        event.full_clean()

        event.recurrence_rule = None
        with self.assertRaises(ValidationError):
            event.full_clean()

    def test_non_recurring_must_not_have_recurrence_rule(self):
        start = timezone.now()
        end = start + timedelta(hours=1)
        rule = RecurrenceRule.objects.create(
            organization=self.organization,
            frequency="DAILY",
            interval=1,
        )
        event = Event(
            organization=self.organization,
            calendar=self.calendar,
            created_by=self.user,
            title="Invalid",
            start_datetime=start,
            end_datetime=end,
            timezone="UTC",
            is_recurring=False,
            recurrence_rule=rule,
        )
        with self.assertRaises(ValidationError):
            event.full_clean()

    def test_duration_and_multi_day_properties(self):
        start = datetime(2026, 1, 1, 10, 0, tzinfo=dt_timezone.utc)
        end = start + timedelta(hours=2)
        event = self._create_basic_event(start_datetime=start, end_datetime=end)
        self.assertEqual(event.duration_minutes, 120)
        self.assertFalse(event.is_multi_day)

        multi_end = datetime(2026, 1, 2, 9, 0, tzinfo=dt_timezone.utc)
        event2 = self._create_basic_event(
            title="Multi",
            start_datetime=start,
            end_datetime=multi_end,
        )
        self.assertTrue(event2.is_multi_day)

    def test_ical_uid_and_etag_generated_on_save(self):
        event = self._create_basic_event()
        self.assertIsNotNone(event.ical_uid)
        self.assertIsNotNone(event.etag)


class EventQuerySetTests(CalendarTestBase):
    def setUp(self):
        super().setUp()
        start = datetime(2026, 1, 15, 10, 0, tzinfo=dt_timezone.utc)
        for i in range(3):
            Event.objects.create(
                organization=self.organization,
                calendar=self.calendar,
                created_by=self.user,
                title=f"Event {i}",
                start_datetime=start + timedelta(days=i),
                end_datetime=start + timedelta(days=i, hours=1),
                timezone="UTC",
            )
        self.deleted_event = Event.objects.create(
            organization=self.organization,
            calendar=self.calendar,
            created_by=self.user,
            title="Deleted",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            timezone="UTC",
            is_deleted=True,
        )

    def test_active_only_returns_non_deleted(self):
        all_count = Event.objects.filter(organization=self.organization).count()
        active_count = Event.objects.active().for_organization(self.organization).count()
        self.assertEqual(all_count - 1, active_count)

    def test_for_calendars_and_in_timerange(self):
        cal_events = Event.objects.active().for_calendars([self.calendar])
        self.assertEqual(cal_events.count(), 3)

        start = datetime(2026, 1, 15, 0, 0, tzinfo=dt_timezone.utc)
        end = datetime(2026, 1, 17, 0, 0, tzinfo=dt_timezone.utc)
        ranged = Event.objects.active().for_organization(self.organization).in_timerange(start, end)
        self.assertEqual(ranged.count(), 2)


class EventAttendeeTests(CalendarTestBase):
    def test_attendee_email_and_display_autofill(self):
        event = Event.objects.create(
            organization=self.organization,
            calendar=self.calendar,
            created_by=self.user,
            title="Meeting",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            timezone="UTC",
        )
        attendee = EventAttendee.objects.create(
            organization=self.organization,
            event=event,
            user=self.user,
            email="",
            display_name="",
            attendee_type="required",
        )
        self.assertEqual(attendee.email, self.user.email)
        self.assertEqual(attendee.display_name, self.user.email)

    def test_rsvp_updates_response_and_timestamp(self):
        event = Event.objects.create(
            organization=self.organization,
            calendar=self.calendar,
            created_by=self.user,
            title="Meeting",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            timezone="UTC",
        )
        attendee = EventAttendee.objects.create(
            organization=self.organization,
            event=event,
            user=self.user,
            email=self.user.email,
            attendee_type="required",
        )
        self.assertEqual(attendee.response_status, "needs_action")
        self.assertIsNone(attendee.responded_at)

        attendee.response_status = "accepted"
        attendee.save()
        attendee.refresh_from_db()
        self.assertEqual(attendee.response_status, "accepted")
        self.assertIsNotNone(attendee.responded_at)


class EventReminderTests(CalendarTestBase):
    def test_reminder_scheduled_time_computed_from_minutes_before(self):
        start = datetime(2026, 1, 15, 10, 0, tzinfo=dt_timezone.utc)
        end = start + timedelta(hours=1)
        event = Event.objects.create(
            organization=self.organization,
            calendar=self.calendar,
            created_by=self.user,
            title="Meeting",
            start_datetime=start,
            end_datetime=end,
            timezone="UTC",
        )
        reminder = EventReminder.objects.create(
            organization=self.organization,
            event=event,
            user=self.user,
            method="notification",
            minutes_before=30,
        )
        expected = start - timedelta(minutes=30)
        self.assertEqual(reminder.scheduled_time, expected)


class CalendarAPITests(CalendarTestBase):
    def _create_project_calendar_for_cross_org_member(self, role: str = "viewer"):
        project = Project.objects.create(
            name="Shared Project",
            organization=self.organization,
            owner=self.user,
            objectives=["awareness"],
            kpis={"ctr": {"target": 0.02}},
        )
        ProjectMember.objects.create(user=self.user, project=project, role="owner", is_active=True)
        ProjectMember.objects.create(user=self.cross_org_user, project=project, role=role, is_active=True)
        project_calendar = Calendar.objects.create(
            organization=self.organization,
            owner=self.user,
            project=project,
            name="Shared Project Calendar",
            timezone="UTC",
            is_primary=False,
        )
        return project, project_calendar

    def test_create_calendar_via_viewset(self):
        view = CalendarViewSet.as_view({"post": "create"})
        payload = {"name": "Personal", "description": "Personal calendar", "color": "#1E88E5", "visibility": "private", "timezone": "UTC", "is_primary": False}
        request = self.factory.post("/api/v1/calendars/", payload, format="json")
        force_authenticate(request, user=self.user)
        response = view(request)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Calendar.objects.filter(organization=self.organization).count(), 3)

    def test_calendar_unique_name_per_owner(self):
        view = CalendarViewSet.as_view({"post": "create"})
        payload = {"name": "My Calendar", "timezone": "UTC"}
        request = self.factory.post("/api/v1/calendars/", payload, format="json")
        force_authenticate(request, user=self.user)
        with self.assertRaises(ValidationError):
            view(request)

    def test_calendar_list_includes_subscriptions_and_visibility_filter(self):
        other_cal = Calendar.objects.create(organization=self.organization, owner=self.other_user, name="Team Calendar", timezone="UTC", visibility="public")
        CalendarSubscription.objects.create(organization=self.organization, user=self.user, calendar=other_cal, is_hidden=False)
        view = CalendarViewSet.as_view({"get": "list"})

        req = self.factory.get("/api/v1/calendars/")
        force_authenticate(req, user=self.user)
        resp = view(req)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        items = resp.data.get("results", resp.data)
        names = sorted([c["name"] for c in items])
        self.assertIn("My Calendar", names)
        self.assertIn("Team Calendar", names)

        req_no_sub = self.factory.get("/api/v1/calendars/?include_subscriptions=false")
        force_authenticate(req_no_sub, user=self.user)
        resp_no_sub = view(req_no_sub)
        items_no_sub = resp_no_sub.data.get("results", resp_no_sub.data)
        names_no_sub = [c["name"] for c in items_no_sub]
        self.assertIn("My Calendar", names_no_sub)
        self.assertNotIn("Team Calendar", names_no_sub)

        req_public = self.factory.get("/api/v1/calendars/?visibility=public")
        force_authenticate(req_public, user=self.user)
        resp_public = view(req_public)
        items_public = resp_public.data.get("results", resp_public.data)
        names_public = [c["name"] for c in items_public]
        self.assertEqual(names_public, ["Team Calendar"])

    def test_calendar_list_with_project_id_includes_own_personal(self):
        project, project_calendar = self._create_project_calendar_for_cross_org_member()
        view = CalendarViewSet.as_view({"get": "list"})

        req = self.factory.get(f"/api/v1/calendars/?project_id={project.id}")
        force_authenticate(req, user=self.user)
        resp = view(req)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        items = resp.data.get("results", resp.data)
        names = {item["name"] for item in items}
        self.assertIn("My Calendar", names)
        self.assertIn(project_calendar.name, names)

        req_member = self.factory.get(f"/api/v1/calendars/?project_id={project.id}")
        force_authenticate(req_member, user=self.cross_org_user)
        resp_member = view(req_member)
        member_names = {item["name"] for item in resp_member.data.get("results", resp_member.data)}
        self.assertIn(project_calendar.name, member_names)
        self.assertNotIn("My Calendar", member_names)

    def test_cross_org_project_member_sees_project_calendar(self):
        _project, project_calendar = self._create_project_calendar_for_cross_org_member(role="viewer")
        view = CalendarViewSet.as_view({"get": "list"})
        req = self.factory.get("/api/v1/calendars/")
        force_authenticate(req, user=self.cross_org_user)
        resp = view(req)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        items = resp.data.get("results", resp.data)
        calendar_ids = [item["id"] for item in items]
        self.assertIn(str(project_calendar.id), calendar_ids)


class EventAPITests(CalendarTestBase):
    def _create_project_calendar_for_cross_org_member(self, role: str = "member") -> Calendar:
        project = Project.objects.create(name=f"Project {role}", organization=self.organization, owner=self.user, objectives=["awareness"], kpis={"ctr": {"target": 0.02}})
        ProjectMember.objects.create(user=self.user, project=project, role="owner", is_active=True)
        ProjectMember.objects.create(user=self.cross_org_user, project=project, role=role, is_active=True)
        return Calendar.objects.create(organization=self.organization, owner=self.user, project=project, name=f"Project {role} Calendar", timezone="UTC", is_primary=False)

    def _create_event_via_api(self) -> Event:
        view = EventViewSet.as_view({"post": "create"})
        payload = {"calendar_id": str(self.calendar.id), "title": "Planning Meeting", "description": "Weekly planning", "start_datetime": "2026-01-15T10:00:00Z", "end_datetime": "2026-01-15T11:00:00Z", "timezone": "UTC", "is_all_day": False, "status": "confirmed", "event_type": "default"}
        request = self.factory.post("/api/v1/events/", payload, format="json")
        force_authenticate(request, user=self.user)
        response = view(request)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return Event.objects.get(id=response.data["id"])

    def test_create_event_happy_path(self):
        event = self._create_event_via_api()
        self.assertEqual(event.title, "Planning Meeting")
        self.assertEqual(event.calendar, self.calendar)
        self.assertEqual(event.created_by, self.user)

    def test_create_event_validation_error_has_standard_error_shape(self):
        view = EventViewSet.as_view({"post": "create"})
        payload = {"calendar_id": "not-a-uuid", "start_datetime": "2026-01-01T10:00:00Z", "end_datetime": "2026-01-01T11:00:00Z", "timezone": "UTC"}
        request = self.factory.post("/api/v1/events/", payload, format="json")
        force_authenticate(request, user=self.user)
        response = view(request)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get("error"), "VALIDATION_ERROR")
        self.assertIn("details", response.data)

    def test_event_soft_delete(self):
        event = self._create_event_via_api()
        view = EventViewSet.as_view({"delete": "destroy"})
        request = self.factory.delete(f"/api/v1/events/{event.id}/")
        force_authenticate(request, user=self.user)
        response = view(request, pk=event.id)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        event.refresh_from_db()
        self.assertTrue(event.is_deleted)

    def test_event_list_pagination_and_filtering(self):
        for i in range(5):
            Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title=f"Event {i}", start_datetime=datetime(2026, 1, 10 + i, 10, 0, tzinfo=dt_timezone.utc), end_datetime=datetime(2026, 1, 10 + i, 11, 0, tzinfo=dt_timezone.utc), timezone="UTC", status="confirmed" if i % 2 == 0 else "tentative")
        view = EventViewSet.as_view({"get": "list"})
        req_page = self.factory.get("/api/v1/events/?page=1")
        force_authenticate(req_page, user=self.user)
        resp_page = view(req_page)
        self.assertEqual(resp_page.status_code, status.HTTP_200_OK)
        data = resp_page.data
        self.assertIn("count", data)
        self.assertIn("results", data)
        self.assertGreater(len(data["results"]), 0)
        self.assertLessEqual(len(data["results"]), data["count"])

        req_filter = self.factory.get("/api/v1/events/?status=confirmed")
        force_authenticate(req_filter, user=self.user)
        resp_filter = view(req_filter)
        self.assertEqual(resp_filter.status_code, status.HTTP_200_OK)
        for item in resp_filter.data["results"]:
            self.assertEqual(item["status"], "confirmed")

    def test_cross_org_member_can_create_event_on_project_calendar(self):
        project_calendar = self._create_project_calendar_for_cross_org_member(role="member")
        view = EventViewSet.as_view({"post": "create"})
        payload = {"calendar_id": str(project_calendar.id), "title": "Cross Org Planning", "start_datetime": "2026-01-16T10:00:00Z", "end_datetime": "2026-01-16T11:00:00Z", "timezone": "UTC", "is_all_day": False, "status": "confirmed", "event_type": "default"}
        request = self.factory.post("/api/v1/events/", payload, format="json")
        force_authenticate(request, user=self.cross_org_user)
        response = view(request)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        event = Event.objects.get(id=response.data["id"])
        self.assertEqual(event.calendar_id, project_calendar.id)
        self.assertEqual(event.created_by_id, self.cross_org_user.id)
        self.assertEqual(event.organization_id, self.organization.id)

    def test_cross_org_viewer_cannot_create_event_on_project_calendar(self):
        project_calendar = self._create_project_calendar_for_cross_org_member(role="viewer")
        view = EventViewSet.as_view({"post": "create"})
        payload = {"calendar_id": str(project_calendar.id), "title": "Cross Org Viewer Create", "start_datetime": "2026-01-16T10:00:00Z", "end_datetime": "2026-01-16T11:00:00Z", "timezone": "UTC", "is_all_day": False, "status": "confirmed", "event_type": "default"}
        request = self.factory.post("/api/v1/events/", payload, format="json")
        force_authenticate(request, user=self.cross_org_user)
        response = view(request)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class SubscriptionAPITests(CalendarTestBase):
    def test_create_and_list_subscription_via_api(self):
        view = SubscriptionListCreateView.as_view()
        payload = {"calendar_id": str(self.other_calendar.id), "color_override": "#E53935", "is_hidden": False, "notification_enabled": True}
        req_create = self.factory.post("/api/v1/subscriptions/", payload, format="json")
        force_authenticate(req_create, user=self.user)
        resp_create = view(req_create)
        self.assertEqual(resp_create.status_code, status.HTTP_201_CREATED)
        req_list = self.factory.get("/api/v1/subscriptions/")
        force_authenticate(req_list, user=self.user)
        resp_list = view(req_list)
        self.assertEqual(resp_list.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp_list.data), 1)

    def test_subscription_patch_and_delete_via_api(self):
        sub = CalendarSubscription.objects.create(organization=self.organization, user=self.user, calendar=self.other_calendar, is_hidden=False)
        detail_view = SubscriptionDetailView.as_view()
        req_patch = self.factory.patch(f"/api/v1/subscriptions/{sub.id}/", {"is_hidden": True}, format="json")
        force_authenticate(req_patch, user=self.user)
        resp_patch = detail_view(req_patch, subscription_id=sub.id)
        self.assertEqual(resp_patch.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertTrue(sub.is_hidden)
        req_del = self.factory.delete(f"/api/v1/subscriptions/{sub.id}/")
        force_authenticate(req_del, user=self.user)
        resp_del = detail_view(req_del, subscription_id=sub.id)
        self.assertEqual(resp_del.status_code, status.HTTP_204_NO_CONTENT)
        sub.refresh_from_db()
        self.assertTrue(sub.is_deleted)


class RecurringEventAPITests(CalendarTestBase):
    def _create_recurring_event(self) -> Event:
        view = EventViewSet.as_view({"post": "create"})
        payload = {"calendar_id": str(self.calendar.id), "title": "Daily Recurring Test", "description": "For instances testing", "start_datetime": "2026-01-15T09:00:00Z", "end_datetime": "2026-01-15T10:00:00Z", "timezone": "UTC", "is_all_day": False, "status": "confirmed", "event_type": "default", "is_recurring": True, "recurrence": {"frequency": "DAILY", "interval": 1}}
        request = self.factory.post("/api/v1/events/", payload, format="json")
        force_authenticate(request, user=self.user)
        response = view(request)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return Event.objects.get(id=response.data["id"])

    def test_instances_modify_and_cancel(self):
        event = self._create_recurring_event()
        instances_view = EventInstancesView.as_view()
        req_ins = self.factory.get(f"/api/v1/events/{event.id}/instances/", {"time_min": "2026-01-15T00:00:00Z", "time_max": "2026-01-20T00:00:00Z", "max_results": 10})
        force_authenticate(req_ins, user=self.user)
        resp_ins = instances_view(req_ins, event_id=event.id)
        self.assertEqual(resp_ins.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(resp_ins.data), 3)

        original_start = "2026-01-15T09:00:00Z"
        modify_view = EventInstanceModifyView.as_view()
        req_mod = self.factory.patch(f"/api/v1/events/{event.id}/instances/modify/", {"title": "Modified Instance Title"}, format="json")
        req_mod.META["QUERY_STRING"] = f"original_start={original_start}"
        force_authenticate(req_mod, user=self.user)
        resp_mod = modify_view(req_mod, event_id=event.id)
        self.assertEqual(resp_mod.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_mod.data.get("title"), "Modified Instance Title")

        cancel_view = EventInstanceCancelView.as_view()
        req_cancel = self.factory.delete(f"/api/v1/events/{event.id}/instances/cancel/")
        req_cancel.META["QUERY_STRING"] = f"original_start={original_start}"
        force_authenticate(req_cancel, user=self.user)
        resp_cancel = cancel_view(req_cancel, event_id=event.id)
        self.assertEqual(resp_cancel.status_code, status.HTTP_204_NO_CONTENT)


class AttendeeAndRSVPAPITests(CalendarTestBase):
    def setUp(self):
        super().setUp()
        self.event = Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title="Meeting", start_datetime=timezone.now(), end_datetime=timezone.now() + timedelta(hours=1), timezone="UTC")

    def test_attendee_crud_and_rsvp(self):
        list_view = EventAttendeeListCreateView.as_view()
        req_list = self.factory.get(f"/api/v1/events/{self.event.id}/attendees/")
        force_authenticate(req_list, user=self.user)
        resp_list = list_view(req_list, event_id=self.event.id)
        self.assertEqual(resp_list.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_list.data, [])

        create_view = EventAttendeeListCreateView.as_view()
        req_create = self.factory.post(f"/api/v1/events/{self.event.id}/attendees/", {"user_id": self.user.id, "attendee_type": "required"}, format="json")
        force_authenticate(req_create, user=self.user)
        resp_create = create_view(req_create, event_id=self.event.id)
        self.assertEqual(resp_create.status_code, status.HTTP_201_CREATED)
        attendee_id = resp_create.data["id"]

        req_list2 = self.factory.get(f"/api/v1/events/{self.event.id}/attendees/")
        force_authenticate(req_list2, user=self.user)
        resp_list2 = list_view(req_list2, event_id=self.event.id)
        self.assertEqual(len(resp_list2.data), 1)

        rsvp_view = EventRSVPView.as_view()
        req_rsvp = self.factory.post(f"/api/v1/events/{self.event.id}/rsvp/", {"response_status": "accepted", "response_comment": "See you"}, format="json")
        force_authenticate(req_rsvp, user=self.user)
        resp_rsvp = rsvp_view(req_rsvp, event_id=self.event.id)
        self.assertEqual(resp_rsvp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_rsvp.data["response_status"], "accepted")

        detail_view = EventAttendeeDetailView.as_view()
        req_delete = self.factory.delete(f"/api/v1/events/{self.event.id}/attendees/{attendee_id}/")
        force_authenticate(req_delete, user=self.user)
        resp_delete = detail_view(req_delete, event_id=self.event.id, attendee_id=attendee_id)
        self.assertEqual(resp_delete.status_code, status.HTTP_204_NO_CONTENT)
        att = EventAttendee.objects.get(id=attendee_id)
        self.assertTrue(att.is_deleted)


class CalendarShareAPITests(CalendarTestBase):
    def setUp(self):
        super().setUp()
        self.view_list = CalendarShareListCreateView.as_view()
        self.view_detail = CalendarShareDetailView.as_view()

    def test_share_create_list_and_delete(self):
        payload = {"user_id": self.other_user.id, "permission": "view_all", "can_invite_others": True}
        req_create = self.factory.post(f"/api/v1/calendars/{self.calendar.id}/shares/", payload, format="json")
        force_authenticate(req_create, user=self.user)
        resp_create = self.view_list(req_create, calendar_id=self.calendar.id)
        self.assertEqual(resp_create.status_code, status.HTTP_201_CREATED)
        share_id = resp_create.data["id"]

        req_list = self.factory.get(f"/api/v1/calendars/{self.calendar.id}/shares/")
        force_authenticate(req_list, user=self.user)
        resp_list = self.view_list(req_list, calendar_id=self.calendar.id)
        self.assertEqual(resp_list.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp_list.data), 1)

        req_get = self.factory.get(f"/api/v1/calendars/{self.calendar.id}/shares/{share_id}/")
        force_authenticate(req_get, user=self.user)
        resp_get = self.view_detail(req_get, calendar_id=self.calendar.id, share_id=share_id)
        self.assertEqual(resp_get.status_code, status.HTTP_200_OK)

        req_del = self.factory.delete(f"/api/v1/calendars/{self.calendar.id}/shares/{share_id}/")
        force_authenticate(req_del, user=self.user)
        resp_del = self.view_detail(req_del, calendar_id=self.calendar.id, share_id=share_id)
        self.assertEqual(resp_del.status_code, status.HTTP_204_NO_CONTENT)
        share = CalendarShare.objects.get(id=share_id)
        self.assertTrue(share.is_deleted)


class CalendarViewsAndFreeBusyTests(CalendarTestBase):
    def setUp(self):
        super().setUp()
        self.event = Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title="Planning Meeting", start_datetime=datetime(2026, 1, 15, 10, 0, tzinfo=dt_timezone.utc), end_datetime=datetime(2026, 1, 15, 11, 0, tzinfo=dt_timezone.utc), timezone="UTC")
        rule = RecurrenceRule.objects.create(organization=self.organization, frequency="DAILY", interval=1)
        Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title="Daily Standup", start_datetime=datetime(2026, 1, 16, 9, 0, tzinfo=dt_timezone.utc), end_datetime=datetime(2026, 1, 16, 10, 0, tzinfo=dt_timezone.utc), timezone="UTC", is_recurring=True, recurrence_rule=rule)

    def test_day_week_month_agenda_views(self):
        day_view = DayView.as_view()
        req_day = self.factory.get("/api/v1/views/day/", {"date": "2026-01-15"})
        force_authenticate(req_day, user=self.user)
        resp_day = day_view(req_day)
        self.assertEqual(resp_day.status_code, status.HTTP_200_OK)
        self.assertIn("events", resp_day.data)

        week_view = WeekView.as_view()
        req_week = self.factory.get("/api/v1/views/week/", {"start_date": "2026-01-15"})
        force_authenticate(req_week, user=self.user)
        resp_week = week_view(req_week)
        self.assertEqual(resp_week.status_code, status.HTTP_200_OK)

        month_view = MonthView.as_view()
        req_month = self.factory.get("/api/v1/views/month/", {"year": "2026", "month": "1"})
        force_authenticate(req_month, user=self.user)
        resp_month = month_view(req_month)
        self.assertEqual(resp_month.status_code, status.HTTP_200_OK)

        agenda_view = AgendaView.as_view()
        req_agenda = self.factory.get("/api/v1/views/agenda/", {"start_date": "2026-01-15T00:00:00Z", "end_date": "2026-01-22T00:00:00Z", "limit": 10})
        force_authenticate(req_agenda, user=self.user)
        resp_agenda = agenda_view(req_agenda)
        self.assertEqual(resp_agenda.status_code, status.HTTP_200_OK)

    def test_freebusy_and_error_cases(self):
        freebusy_view = FreeBusyView.as_view()
        req_fb = self.factory.post("/api/v1/freebusy/", {"time_min": "2026-01-15T00:00:00Z", "time_max": "2026-01-20T00:00:00Z", "calendar_ids": [str(self.calendar.id)]}, format="json")
        force_authenticate(req_fb, user=self.user)
        resp_fb = freebusy_view(req_fb)
        self.assertEqual(resp_fb.status_code, status.HTTP_200_OK)
        self.assertIn(str(self.calendar.id), resp_fb.data["calendars"])

        bad_day_req = self.factory.get("/api/v1/views/day/")
        force_authenticate(bad_day_req, user=self.user)
        bad_day_resp = DayView.as_view()(bad_day_req)
        self.assertEqual(bad_day_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(bad_day_resp.data.get("error"), "BAD_REQUEST")

        bad_fb_req = self.factory.post("/api/v1/freebusy/", {"time_min": "2026-01-20T00:00:00Z", "time_max": "2026-01-15T00:00:00Z", "calendar_ids": [str(self.calendar.id)]}, format="json")
        force_authenticate(bad_fb_req, user=self.user)
        bad_fb_resp = freebusy_view(bad_fb_req)
        self.assertEqual(bad_fb_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(bad_fb_resp.data.get("error"), "BAD_REQUEST")

    def test_day_view_with_many_events_is_responsive(self):
        for i in range(30):
            Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title=f"Bulk {i}", start_datetime=datetime(2026, 1, 15, 8, 0, tzinfo=dt_timezone.utc) + timedelta(minutes=10 * i), end_datetime=datetime(2026, 1, 15, 9, 0, tzinfo=dt_timezone.utc) + timedelta(minutes=10 * i), timezone="UTC")
        day_view = DayView.as_view()
        req = self.factory.get("/api/v1/views/day/", {"date": "2026-01-15"})
        force_authenticate(req, user=self.user)
        resp = day_view(req)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(resp.data["events"]), 30)


class ReminderAPITests(CalendarTestBase):
    def test_create_and_list_reminders(self):
        event = Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title="Meeting", start_datetime=datetime(2026, 1, 15, 10, 0, tzinfo=dt_timezone.utc), end_datetime=datetime(2026, 1, 15, 11, 0, tzinfo=dt_timezone.utc), timezone="UTC")
        view = EventReminderListCreateView.as_view()
        req_create = self.factory.post(f"/api/v1/events/{event.id}/reminders/", {"method": "notification", "minutes_before": 30}, format="json")
        force_authenticate(req_create, user=self.user)
        resp_create = view(req_create, event_id=event.id)
        self.assertEqual(resp_create.status_code, status.HTTP_201_CREATED)
        req_list = self.factory.get(f"/api/v1/events/{event.id}/reminders/")
        force_authenticate(req_list, user=self.user)
        resp_list = view(req_list, event_id=event.id)
        self.assertEqual(resp_list.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp_list.data), 1)


class ETagConcurrencyTests(CalendarTestBase):
    def test_etag_if_match_on_event_update(self):
        event = Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title="Meeting", start_datetime=datetime(2026, 1, 15, 10, 0, tzinfo=dt_timezone.utc), end_datetime=datetime(2026, 1, 15, 11, 0, tzinfo=dt_timezone.utc), timezone="UTC")
        event.refresh_from_db()
        view = EventViewSet.as_view({"patch": "partial_update"})
        req_ok = self.factory.patch(f"/api/v1/events/{event.id}/", {"description": "Updated via ETag test"}, format="json")
        req_ok.META["HTTP_IF_MATCH"] = event.etag or ""
        force_authenticate(req_ok, user=self.user)
        resp_ok = view(req_ok, pk=event.id)
        self.assertEqual(resp_ok.status_code, status.HTTP_200_OK)
        new_etag = resp_ok.data.get("etag")
        self.assertIsNotNone(new_etag)
        event.refresh_from_db()
        self.assertEqual(event.etag, new_etag)
        req_fail = self.factory.patch(f"/api/v1/events/{event.id}/", {"description": "Should fail"}, format="json")
        req_fail.META["HTTP_IF_MATCH"] = new_etag + "_stale"
        force_authenticate(req_fail, user=self.user)
        resp_fail = view(req_fail, pk=event.id)
        self.assertEqual(resp_fail.status_code, status.HTTP_412_PRECONDITION_FAILED)
        self.assertEqual(resp_fail.data.get("error"), "PRECONDITION_FAILED")


class TimezoneAndDSTTests(CalendarTestBase):
    def test_non_utc_timezone_event_visible_in_views(self):
        start = datetime(2026, 3, 10, 14, 0, tzinfo=dt_timezone.utc)
        end = start + timedelta(hours=1)
        Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title="NYC Meeting", start_datetime=start, end_datetime=end, timezone="America/New_York")
        day_view = DayView.as_view()
        req = self.factory.get("/api/v1/views/day/", {"date": "2026-03-10"})
        force_authenticate(req, user=self.user)
        resp = day_view(req)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = [e["title"] for e in resp.data["events"]]
        self.assertIn("NYC Meeting", titles)

    def test_event_across_dst_boundary_is_returned(self):
        start = datetime(2026, 3, 8, 6, 0, tzinfo=dt_timezone.utc)
        end = start + timedelta(hours=4)
        Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title="DST Crossing", start_datetime=start, end_datetime=end, timezone="America/New_York")
        agenda_view = AgendaView.as_view()
        req = self.factory.get("/api/v1/views/agenda/", {"start_date": "2026-03-08T00:00:00Z", "end_date": "2026-03-09T00:00:00Z"})
        force_authenticate(req, user=self.user)
        resp = agenda_view(req)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = [e["title"] for e in resp.data["events"]]
        self.assertIn("DST Crossing", titles)


class AuxiliaryModelsTests(CalendarTestBase):
    def test_event_category_validation_and_unique_constraint(self):
        cat = EventCategory.objects.create(organization=self.organization, user=self.user, name="Important", color="#FF0000")
        self.assertIn("Important", str(cat))
        bad_cat = EventCategory(organization=self.other_organization, user=self.user, name="Mismatch")
        with self.assertRaises(ValidationError):
            bad_cat.full_clean()
        with self.assertRaises(ValidationError):
            EventCategory.objects.create(organization=self.organization, user=self.user, name="Important")
        cat.is_deleted = True
        cat.save()
        cat2 = EventCategory.objects.create(organization=self.organization, user=self.user, name="Important")
        self.assertTrue(EventCategory.objects.filter(id=cat2.id).exists())

    def test_event_category_assignment_validation(self):
        event = Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title="Categorized event", start_datetime=timezone.now(), end_datetime=timezone.now() + timedelta(hours=1), timezone="UTC")
        category = EventCategory.objects.create(organization=self.organization, user=self.user, name="Category")
        assignment = EventCategoryAssignment.objects.create(organization=self.organization, event=event, category=category)
        self.assertIn("->", str(assignment))
        other_org = Organization.objects.create(name="OtherOrg2", slug="other-org2")
        bad_assignment = EventCategoryAssignment(organization=other_org, event=event, category=category)
        with self.assertRaises(ValidationError):
            bad_assignment.full_clean()

    def test_calendar_settings_validation_and_unique_per_user(self):
        settings_obj = CalendarSettings.objects.create(organization=self.organization, user=self.user, default_view="week", week_start=1, time_format="24h", timezone="UTC")
        self.assertIn(self.user.email, str(settings_obj))
        other_org = Organization.objects.create(name="OtherOrg3", slug="other-org3")
        bad_settings = CalendarSettings(organization=other_org, user=self.user)
        with self.assertRaises(ValidationError):
            bad_settings.full_clean()
        with self.assertRaises(ValidationError):
            CalendarSettings.objects.create(organization=self.organization, user=self.user)

    def test_notification_validation_and_mark_as_read(self):
        event = Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title="Notif Event", start_datetime=timezone.now(), end_datetime=timezone.now() + timedelta(hours=1), timezone="UTC")
        notif = Notification.objects.create(organization=self.organization, user=self.user, notification_type="event_reminder", title="Reminder", message="Event is starting soon", event=event, calendar=self.calendar)
        self.assertIn("Event reminder", str(notif))
        other_org = Organization.objects.create(name="OtherOrg4", slug="other-org4")
        bad_notif = Notification(organization=other_org, user=self.user, notification_type="event_updated", title="Bad", message="Bad org")
        with self.assertRaises(ValidationError):
            bad_notif.full_clean()
        self.assertFalse(notif.is_read)
        self.assertIsNone(notif.read_at)
        notif.mark_as_read()
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)
        self.assertIsNotNone(notif.read_at)


class EventSearchTests(CalendarTestBase):
    def test_search_by_query_and_calendar_filter(self):
        Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title="Planning Meeting", description="Weekly planning", start_datetime=datetime(2026, 1, 15, 10, 0, tzinfo=dt_timezone.utc), end_datetime=datetime(2026, 1, 15, 11, 0, tzinfo=dt_timezone.utc), timezone="UTC")
        Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title="Other Event", description="Something else", start_datetime=datetime(2026, 1, 16, 10, 0, tzinfo=dt_timezone.utc), end_datetime=datetime(2026, 1, 16, 11, 0, tzinfo=dt_timezone.utc), timezone="UTC")
        view = EventSearchView.as_view()
        req = self.factory.get("/api/v1/events/search/", {"q": "Planning", "calendar_ids": str(self.calendar.id)})
        force_authenticate(req, user=self.user)
        resp = view(req)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)

    def test_search_short_query_returns_empty(self):
        Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title="X", description="Too short query", start_datetime=datetime(2026, 1, 15, 10, 0, tzinfo=dt_timezone.utc), end_datetime=datetime(2026, 1, 15, 11, 0, tzinfo=dt_timezone.utc), timezone="UTC")
        view = EventSearchView.as_view()
        req = self.factory.get("/api/v1/events/search/", {"q": "X"})
        force_authenticate(req, user=self.user)
        resp = view(req)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 0)


class ExceptionsAndPermissionsTests(CalendarTestBase):
    def test_calendar_error_response_basic_shape(self):
        response = calendar_error_response(error="TEST_ERROR", message="Something went wrong", status_code=418, details=[{"field": "x", "reason": "constraint_violation", "message": "bad", "metadata": {}}])
        self.assertEqual(response.status_code, 418)
        data = response.data
        self.assertEqual(data["error"], "TEST_ERROR")
        self.assertEqual(data["message"], "Something went wrong")
        self.assertIn("request_id", data)
        self.assertIn("timestamp", data)
        self.assertIn("details", data)

    def test_build_details_from_errors_dict_and_list(self):
        errors_dict = {"field": ["msg1", "msg2"]}
        details = _build_details_from_errors(errors_dict)
        self.assertEqual(len(details), 2)
        self.assertEqual(details[0]["field"], "field")
        self.assertEqual(details[0]["message"], "msg1")
        errors_list = ["err1", "err2"]
        details = _build_details_from_errors(errors_list)
        self.assertEqual(len(details), 2)
        self.assertEqual(details[0]["field"], "")

    def test_calendar_exception_handler_for_calendars_view(self):
        from rest_framework.exceptions import ValidationError as DRFValidationError
        from rest_framework.views import APIView

        class DummyCalendarView(APIView):
            def get(self, request):
                raise DRFValidationError({"field": ["bad"]})

        view = DummyCalendarView()
        request = self.factory.get("/api/v1/calendars/")
        force_authenticate(request, user=self.user)
        try:
            view.get(request)
        except DRFValidationError as exc:
            context = {"view": view, "request": request}
            response = calendar_exception_handler(exc, context)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        data = response.data
        self.assertEqual(data["error"], "VALIDATION_ERROR")
        self.assertIn("details", data)

    def test_is_authenticated_in_organization_permission(self):
        perm = IsAuthenticatedInOrganization()
        request = self.factory.get("/api/v1/calendars/")
        request.user = auth_models.AnonymousUser()
        self.assertFalse(perm.has_permission(request, view=None))
        user = User.objects.create_user(email="noorg@example.com", password="test1234", username="noorg")
        request.user = user
        self.assertFalse(perm.has_permission(request, view=None))
        user.organization = self.organization
        user.save()
        request.user = user
        self.assertTrue(perm.has_permission(request, view=None))

    def test_calendar_access_permission_owner_and_share(self):
        perm = CalendarAccessPermission()
        request = self.factory.get("/api/v1/calendars/")
        request.user = self.user
        owner_has = perm.has_object_permission(request, view=type("V", (), {"required_permission": "manage"})(), obj=self.calendar)
        self.assertTrue(owner_has)
        CalendarShare.objects.create(organization=self.organization, calendar=self.calendar, shared_with=self.other_user, permission="view_all")
        request.user = self.other_user
        view_all = perm.has_object_permission(request, view=type("V", (), {"required_permission": "view_all"})(), obj=self.calendar)
        self.assertTrue(view_all)
        edit_perm = perm.has_object_permission(request, view=type("V", (), {"required_permission": "edit"})(), obj=self.calendar)
        self.assertFalse(edit_perm)

    def test_event_access_permission(self):
        event = Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title="Meeting", start_datetime=timezone.now(), end_datetime=timezone.now() + timedelta(hours=1), timezone="UTC")
        perm = EventAccessPermission()
        request = self.factory.get("/api/v1/events/")
        request.user = self.user
        has_perm = perm.has_object_permission(request, view=type("V", (), {"required_permission": "view_all"})(), obj=event)
        self.assertTrue(has_perm)

    def test_subscription_owner_permission(self):
        sub = CalendarSubscription.objects.create(organization=self.organization, user=self.user, calendar=self.calendar)
        perm = SubscriptionOwnerPermission()
        request = self.factory.get("/api/v1/subscriptions/")
        request.user = self.user
        self.assertTrue(perm.has_object_permission(request, view=None, obj=sub))
        request.user = self.other_user
        self.assertFalse(perm.has_object_permission(request, view=None, obj=sub))

    def test_unauthenticated_access_is_blocked(self):
        cal_view = CalendarViewSet.as_view({"get": "list"})
        req_cal = self.factory.get("/api/v1/calendars/")
        resp_cal = cal_view(req_cal)
        self.assertIn(resp_cal.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        evt_view = EventViewSet.as_view({"get": "list"})
        req_evt = self.factory.get("/api/v1/events/")
        resp_evt = evt_view(req_evt)
        self.assertIn(resp_evt.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_event_access_denied_for_non_shared_user(self):
        event = Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title="Private Event", start_datetime=timezone.now(), end_datetime=timezone.now() + timedelta(hours=1), timezone="UTC")
        view = EventViewSet.as_view({"get": "retrieve"})
        req = self.factory.get(f"/api/v1/events/{event.id}/")
        force_authenticate(req, user=self.other_user)
        resp = view(req, pk=event.id)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_attendee_access_denied_for_non_shared_user(self):
        event = Event.objects.create(organization=self.organization, calendar=self.calendar, created_by=self.user, title="Private Event", start_datetime=timezone.now(), end_datetime=timezone.now() + timedelta(hours=1), timezone="UTC")
        view = EventAttendeeListCreateView.as_view()
        req = self.factory.get(f"/api/v1/events/{event.id}/attendees/")
        force_authenticate(req, user=self.other_user)
        resp = view(req, event_id=event.id)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class SignalTests(TestCase):
    def test_no_calendar_created_automatically_for_new_user(self):
        org = Organization.objects.create(name="Test Org", slug="test-org")
        user = User.objects.create_user(email="newuser@example.com", password="testpass", username="newuser", organization=org)
        calendars = Calendar.objects.filter(organization=org, owner=user, is_deleted=False)
        self.assertEqual(calendars.count(), 0)

    def test_no_calendar_created_if_user_has_no_organization(self):
        user = User.objects.create_user(email="noorg@example.com", password="testpass", username="noorg", organization=None)
        calendars = Calendar.objects.filter(owner=user)
        self.assertEqual(calendars.count(), 0)

    def test_user_update_does_not_create_calendar(self):
        org = Organization.objects.create(name="Test Org", slug="test-org")
        user = User.objects.create_user(email="updateuser@example.com", password="testpass", username="updateuser", organization=org)
        initial_count = Calendar.objects.filter(owner=user).count()
        self.assertEqual(initial_count, 0)
        user.email = "updated@example.com"
        user.save()
        final_count = Calendar.objects.filter(owner=user).count()
        self.assertEqual(final_count, 0)


class CalendarEventModelTests(TestCase):
    """
    Test the validation logic of the CalendarEvent model.
    """

    def setUp(self):
        self.organization = Organization.objects.create(name="Test Org", slug="test-org-cal-event")
        self.user = User.objects.create_user(email="test@example.com", password="test1234", username="testuser_cal", organization=self.organization)
        self.project = Project.objects.create(name="Test Project", organization=self.organization, owner=self.user, objectives=["awareness"], kpis={"ctr": {"target": 0.02}})

    def test_decision_event_requires_decision(self):
        from calendars.models import CalendarEvent
        from django.core.exceptions import ValidationError
        event = CalendarEvent(organization=self.organization, event_type=CalendarEvent.EventType.DECISION, title="Test", start_time=timezone.now())
        with self.assertRaises(ValidationError):
            event.clean()

    def test_task_event_requires_task(self):
        from calendars.models import CalendarEvent
        from django.core.exceptions import ValidationError
        event = CalendarEvent(organization=self.organization, event_type=CalendarEvent.EventType.TASK, title="Test", start_time=timezone.now())
        with self.assertRaises(ValidationError):
            event.clean()

    def test_decision_review_event_requires_decision(self):
        from calendars.models import CalendarEvent
        from django.core.exceptions import ValidationError
        event = CalendarEvent(organization=self.organization, event_type=CalendarEvent.EventType.DECISION_REVIEW, title="Test", start_time=timezone.now())
        with self.assertRaises(ValidationError):
            event.clean()


class CalendarEventSignalTests(TestCase):
    """
    Test CalendarEvent auto-generation from Decision and Task signals,
    including bug fixes from SMP-400 QA phase 7.
    """

    def setUp(self):
        from calendars.models import CalendarEvent
        self.CalendarEvent = CalendarEvent
        self.organization = Organization.objects.create(name="Signal Test Org", slug="signal-test-org")
        self.user = User.objects.create_user(email="signal@example.com", password="test1234", username="signaluser", organization=self.organization)
        self.project = Project.objects.create(name="Signal Project", organization=self.organization, owner=self.user, objectives=["awareness"], kpis={"ctr": {"target": 0.02}})

    def _create_decision(self, **kwargs):
        from decision.models import Decision
        defaults = {"project": self.project, "author": self.user, "title": "Test Decision", "project_seq": None}
        defaults.update(kwargs)
        return Decision.objects.create(**defaults)

    def _create_task(self, **kwargs):
        from task.models import Task
        defaults = {"summary": "Test Task", "project": self.project, "owner": self.user, "type": "execution", "due_date": timezone.now().date()}
        defaults.update(kwargs)
        return Task.objects.create(**defaults)

    # ── Original tests ────────────────────────────────────────────────────────

    def test_decision_event_created_on_decision_save(self):
        decision = self._create_decision()
        events = self.CalendarEvent.objects.filter(event_type=self.CalendarEvent.EventType.DECISION, decision=decision)
        self.assertEqual(events.count(), 1)

    def test_decision_event_updated_on_decision_update(self):
        decision = self._create_decision()
        decision.title = "Updated Decision"
        decision.save()
        events = self.CalendarEvent.objects.filter(event_type=self.CalendarEvent.EventType.DECISION, decision=decision)
        self.assertEqual(events.count(), 1)
        self.assertIn("Updated Decision", events.first().title)

    def test_task_event_created_on_task_with_due_date(self):
        task = self._create_task()
        events = self.CalendarEvent.objects.filter(event_type=self.CalendarEvent.EventType.TASK, task=task)
        self.assertEqual(events.count(), 1)

    def test_task_event_not_created_without_due_date(self):
        task = self._create_task(due_date=None)
        events = self.CalendarEvent.objects.filter(event_type=self.CalendarEvent.EventType.TASK, task=task)
        self.assertEqual(events.count(), 0)

    def test_decision_review_event_created_on_approved_at(self):
        decision = self._create_decision(approved_at=timezone.now())
        events = self.CalendarEvent.objects.filter(event_type=self.CalendarEvent.EventType.DECISION_REVIEW, decision=decision)
        self.assertEqual(events.count(), 1)

    def test_no_duplicate_events_on_multiple_saves(self):
        task = self._create_task()
        task.summary = "Updated Task"
        task.save()
        task.summary = "Updated Task Again"
        task.save()
        events = self.CalendarEvent.objects.filter(event_type=self.CalendarEvent.EventType.TASK, task=task)
        self.assertEqual(events.count(), 1)

    # ── Bug 1: Task event continuous time range ───────────────────────────────

    def test_task_event_has_start_and_end_time_when_both_dates_set(self):
        """
        Bug 1: When a Task has both start_date and due_date, the CalendarEvent
        should span start_date 00:00 to due_date 23:59 as a continuous multi-day event.
        """
        from datetime import date
        start = date(2026, 4, 10)
        end = date(2026, 4, 12)
        task = self._create_task(start_date=start, due_date=end)
        event = self.CalendarEvent.objects.get(event_type=self.CalendarEvent.EventType.TASK, task=task)
        self.assertIsNotNone(event.start_time)
        self.assertIsNotNone(event.end_time)
        self.assertEqual(event.start_time.date(), start)
        self.assertEqual(event.end_time.date(), end)
        self.assertGreater(event.end_time, event.start_time)

    def test_task_event_single_day_when_only_start_date(self):
        """
        Bug 1: When a Task has only start_date (no due_date), the CalendarEvent
        should be a single-day event on that date.
        """
        from datetime import date
        start = date(2026, 4, 10)
        task = self._create_task(start_date=start, due_date=None)
        event = self.CalendarEvent.objects.get(event_type=self.CalendarEvent.EventType.TASK, task=task)
        self.assertEqual(event.start_time.date(), start)
        self.assertEqual(event.end_time.date(), start)

    def test_task_event_single_day_when_only_due_date(self):
        """
        Bug 1: Backward compatibility — when a Task has only due_date,
        the CalendarEvent should be a single-day event on due_date.
        """
        from datetime import date
        due = date(2026, 4, 12)
        task = self._create_task(start_date=None, due_date=due)
        event = self.CalendarEvent.objects.get(event_type=self.CalendarEvent.EventType.TASK, task=task)
        self.assertEqual(event.start_time.date(), due)
        self.assertEqual(event.end_time.date(), due)

    def test_task_event_time_range_updates_when_dates_change(self):
        """
        Bug 1: When a Task's due_date changes, the CalendarEvent end_time
        should update on the next save.
        """
        from datetime import date
        task = self._create_task(start_date=date(2026, 4, 10), due_date=date(2026, 4, 12))
        task.due_date = date(2026, 4, 15)
        task.save()
        event = self.CalendarEvent.objects.get(event_type=self.CalendarEvent.EventType.TASK, task=task)
        self.assertEqual(event.end_time.date(), date(2026, 4, 15))

    def test_task_description_patch_handles_long_calendar_title_projection(self):
        """
        Updating a task description should not fail because the derived calendar
        title adds project/status text around an already long task summary.
        """
        from datetime import date
        from task.models import Task
        from task.views import TaskViewSet

        ProjectMember.objects.get_or_create(
            user=self.user,
            project=self.project,
            defaults={"role": "owner", "is_active": True},
        )
        task = self._create_task(summary="A" * 255, due_date=None)
        Task.objects.filter(pk=task.pk).update(due_date=date(2026, 4, 12))
        task = Task.objects.get(pk=task.pk)

        request = APIRequestFactory().patch(
            f"/api/tasks/{task.slug}/",
            {"description": "Updated description only"},
            format="json",
        )
        force_authenticate(request, user=self.user)
        response = TaskViewSet.as_view({"patch": "partial_update"})(request, pk=task.slug)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        updated_task = Task.objects.get(pk=task.pk)
        self.assertEqual(updated_task.description, "Updated description only")
        event = self.CalendarEvent.objects.get(
            event_type=self.CalendarEvent.EventType.TASK,
            task=updated_task,
        )
        max_title_length = self.CalendarEvent._meta.get_field("title").max_length
        self.assertLessEqual(len(event.title), max_title_length)
        self.assertIn(f"[{self.project.name}]", event.title)
        self.assertIn(updated_task.get_status_display(), event.title)

    # ── Bug 3: Deleted events removed from calendar ───────────────────────────

    def test_calendar_event_deleted_when_task_deleted(self):
        """
        Bug 3: When a Task is hard-deleted, its CalendarEvent should also be
        removed via the post_delete signal.
        """
        task = self._create_task()
        task_id = task.id
        self.assertEqual(self.CalendarEvent.objects.filter(task=task).count(), 1)
        task.delete()
        self.assertEqual(self.CalendarEvent.objects.filter(task_id=task_id).count(), 0)

    def test_calendar_event_not_recreated_after_decision_soft_delete(self):
        """
        Bug 3 (root cause): When a Decision is soft-deleted, the post_save
        signal must NOT recreate the CalendarEvent.

        Root cause: destroy() deleted CalendarEvent then called decision.save(),
        which fired the signal and recreated the event. The fix adds an
        is_deleted guard at the top of the signal handler.
        """
        decision = self._create_decision()
        self.assertEqual(self.CalendarEvent.objects.filter(decision=decision).count(), 1)
        # Simulate DecisionViewSet.destroy():
        self.CalendarEvent.objects.filter(decision=decision).delete()
        self.assertEqual(self.CalendarEvent.objects.filter(decision=decision).count(), 0)
        decision.is_deleted = True
        decision.save(update_fields=["is_deleted", "updated_at"])
        # CalendarEvent must NOT be recreated by the signal
        self.assertEqual(
            self.CalendarEvent.objects.filter(decision=decision).count(), 0,
            "CalendarEvent was recreated after soft-delete — is_deleted guard missing in signal"
        )

    def test_signal_skips_event_generation_for_soft_deleted_decision(self):
        """
        Bug 3: The post_save signal should return early when
        Decision.is_deleted=True, so no CalendarEvent is created or updated.
        """
        decision = self._create_decision()
        decision.is_deleted = True
        decision.title = "Should not appear on calendar"
        decision.save()
        events = self.CalendarEvent.objects.filter(decision=decision)
        for event in events:
            self.assertNotIn("Should not appear on calendar", event.title, "Signal updated CalendarEvent for a soft-deleted Decision")

    # ── Bug 4: Display actual titles, not IDs ─────────────────────────────────

    def test_decision_event_title_uses_decision_title(self):
        """Bug 4: CalendarEvent title should use the Decision's actual title."""
        decision = self._create_decision(title="My Important Decision")
        event = self.CalendarEvent.objects.get(event_type=self.CalendarEvent.EventType.DECISION, decision=decision)
        self.assertIn("My Important Decision", event.title)

    def test_decision_event_title_uses_context_summary_when_no_title(self):
        """
        Bug 4: When Decision has no title, CalendarEvent should fall back to
        context_summary excerpt rather than showing a raw ID.
        """
        decision = self._create_decision(title=None, context_summary="This is a very important context summary for the decision")
        event = self.CalendarEvent.objects.get(event_type=self.CalendarEvent.EventType.DECISION, decision=decision)
        self.assertNotEqual(event.title, f"Decision #{decision.id}")
        self.assertIn("This is a very important", event.title)

    def test_decision_event_title_includes_project_prefix(self):
        """Bug 4: CalendarEvent title should include project name prefix [project_name] title."""
        decision = self._create_decision(title="Budget Decision")
        event = self.CalendarEvent.objects.get(event_type=self.CalendarEvent.EventType.DECISION, decision=decision)
        self.assertIn(f"[{self.project.name}]", event.title)
        self.assertIn("Budget Decision", event.title)

    def test_task_event_title_uses_task_summary(self):
        """Bug 4: CalendarEvent title should use the Task's actual summary."""
        task = self._create_task(summary="Deploy new feature")
        event = self.CalendarEvent.objects.get(event_type=self.CalendarEvent.EventType.TASK, task=task)
        self.assertIn("Deploy new feature", event.title)

    def test_task_event_title_includes_project_prefix(self):
        """Bug 4: Task CalendarEvent title should include the project name prefix."""
        task = self._create_task(summary="Weekly report")
        event = self.CalendarEvent.objects.get(event_type=self.CalendarEvent.EventType.TASK, task=task)
        self.assertIn(f"[{self.project.name}]", event.title)

    def test_decision_review_event_title_uses_review_prefix(self):
        """
        Bug 4: Decision Review CalendarEvent should have a "Review:" prefix
        so users can distinguish it from a regular Decision event.
        """
        decision = self._create_decision(title="Q4 Strategy", approved_at=timezone.now())
        event = self.CalendarEvent.objects.get(event_type=self.CalendarEvent.EventType.DECISION_REVIEW, decision=decision)
        self.assertIn("Review:", event.title)
        self.assertIn("Q4 Strategy", event.title)

    def test_context_summary_truncated_to_50_chars_in_title(self):
        """
        Bug 4: When falling back to context_summary, the title should be
        truncated at 50 characters with "..." appended if the summary is longer.
        """
        long_summary = "A" * 60  # 60 characters, exceeds the 50-char limit
        decision = self._create_decision(title=None, context_summary=long_summary)
        event = self.CalendarEvent.objects.get(event_type=self.CalendarEvent.EventType.DECISION, decision=decision)
        self.assertIn("...", event.title)

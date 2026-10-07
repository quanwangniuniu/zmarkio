"""
Tests for owner-facing booking link CRUD.

The public endpoints are covered separately; this file is about the management
side, where the properties that matter are ownership scoping and not letting a
caller set owner/organization from the request body.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from calendars.models import BookingLink, Calendar, CalendarSettings
from core.models import Organization, Project, ProjectMember
from customer.models import Customer

User = get_user_model()

LIST_URL = "/api/booking-links/"


def error_fields(response) -> list[str]:
    """
    Field names from the app's error envelope.

    calendars uses a custom exception handler, so validation errors arrive as
    {"error": ..., "details": [{"field": ...}]} rather than DRF's default
    {field: [messages]}.
    """
    return [entry.get("field") for entry in response.json().get("details", [])]

# NOTE: unlike test_public_booking, fixtures here are created on the default
# schema. These endpoints are authenticated, and DRF's force_authenticate sets
# the user at the view layer — TenantSchemaMiddleware never sees a JWT, so it
# resolves to `public` and the request looks for rows there.


class BookingLinkCrudTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Crud Org", slug="crud-org")
        self.user = User.objects.create_user(
            username="crudowner",
            email="owner@crud.com",
            password="x",
            organization=self.org,
        )
        self.colleague = User.objects.create_user(
            username="colleague",
            email="colleague@crud.com",
            password="x",
            organization=self.org,
        )
        self.calendar = Calendar.objects.create(
            organization=self.org,
            owner=self.user,
            name="Primary",
            timezone="UTC",
            is_primary=True,
        )
        self.colleague_calendar = Calendar.objects.create(
            organization=self.org,
            owner=self.colleague,
            name="Colleague",
            timezone="UTC",
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _payload(self, **overrides):
        payload = {
            "slug": "intro-call",
            "title": "Intro Call",
            "calendar_id": str(self.calendar.id),
            "duration_minutes": 30,
        }
        payload.update(overrides)
        return payload

    def _create(self, **overrides):
        return self.client.post(LIST_URL, self._payload(**overrides), format="json")

    def test_owner_can_create_a_link(self):
        response = self._create()
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert response.json()["slug"] == "intro-call"

    def test_owner_and_organization_come_from_the_request(self):
        response = self.client.post(
            LIST_URL,
            {**self._payload(), "owner": self.colleague.id, "organization": 999},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        link = BookingLink.objects.get(slug="intro-call")
        assert link.owner == self.user
        assert link.organization == self.org

    def test_response_carries_the_owning_org_slug(self):
        # The public URL is /book/<org>/<slug>, and the org is the *user's*, not
        # whatever project happens to be active — a user can belong to projects
        # in other organisations. The client must not have to guess it, or the
        # copied link 404s.
        response = self._create()
        assert response.json()["organization_slug"] == self.org.slug

    def test_organization_slug_cannot_be_set_by_the_client(self):
        response = self.client.post(
            LIST_URL,
            {**self._payload(), "organization_slug": "somewhere-else"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["organization_slug"] == self.org.slug

    def test_a_non_primary_calendar_is_allowed(self):
        # `is_primary` is only ever set by the Google connect flow, so requiring
        # it would block booking links for anyone without Google connected —
        # even though availability works from the in-app calendar alone.
        secondary = Calendar.objects.create(
            organization=self.org,
            owner=self.user,
            name="Secondary",
            timezone="UTC",
            is_primary=False,
        )
        response = self._create(calendar_id=str(secondary.id))
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["scope"] == "personal"
        assert response.json()["syncs_to_google"] is True

    def test_primary_calendar_reports_google_sync(self):
        response = self._create()
        assert response.json()["scope"] == "personal"
        assert response.json()["syncs_to_google"] is True

    def test_create_without_calendar_id_provisions_a_personal_calendar(self):
        self.calendar.delete()
        payload = self._payload()
        del payload["calendar_id"]
        response = self.client.post(LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert response.json()["scope"] == "personal"
        created = Calendar.objects.get(pk=response.json()["calendar"])
        assert created.owner_id == self.user.id
        assert created.project_id is None
        assert created.name == "My Calendar"

    def test_a_project_calendar_the_user_does_not_own_is_allowed(self):
        # Calendars here are often project-scoped and owned by whoever made
        # them; an ownership check locks out everyone else on the project.
        from core.models import Project, ProjectMember

        project = Project.objects.create(name="Shared", organization=self.org)
        ProjectMember.objects.create(project=project, user=self.user, is_active=True)
        project_calendar = Calendar.objects.create(
            organization=self.org,
            owner=self.colleague,
            name="Project Calendar",
            timezone="UTC",
            project=project,
        )
        response = self._create(calendar_id=str(project_calendar.id))
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["scope"] == "team"
        assert response.json()["syncs_to_google"] is False

    def test_cannot_point_a_link_at_an_inaccessible_calendar(self):
        # A colleague's personal calendar, on no shared project.
        response = self._create(calendar_id=str(self.colleague_calendar.id))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "calendar_id" in error_fields(response)

    def test_duplicate_slug_is_a_readable_400(self):
        self._create()
        response = self._create(title="Another")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "title" in error_fields(response)

    def test_links_are_listed_newest_first(self):
        older = self._create(slug="older", title="Zebra")
        newer = self._create(slug="newer", title="Apple")
        assert older.status_code == status.HTTP_201_CREATED
        assert newer.status_code == status.HTTP_201_CREATED
        BookingLink.objects.filter(slug="older").update(
            created_at=timezone.now() - timedelta(days=1)
        )
        listed = self.client.get(LIST_URL)
        assert listed.status_code == status.HTTP_200_OK
        assert [row["slug"] for row in listed.json()] == ["newer", "older"]

    def test_list_only_returns_the_callers_links(self):
        self._create()
        self.client.force_authenticate(user=self.colleague)
        response = self.client.get(LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == []

    def test_colleague_cannot_read_or_delete_another_users_link(self):
        link_id = self._create().json()["id"]
        self.client.force_authenticate(user=self.colleague)
        assert (
            self.client.get(f"{LIST_URL}{link_id}/").status_code
            == status.HTTP_404_NOT_FOUND
        )
        assert (
            self.client.delete(f"{LIST_URL}{link_id}/").status_code
            == status.HTTP_404_NOT_FOUND
        )

    def test_owner_can_update_rules(self):
        link_id = self._create().json()["id"]
        response = self.client.patch(
            f"{LIST_URL}{link_id}/",
            {"duration_minutes": 45, "min_notice_minutes": 120},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["duration_minutes"] == 45
        assert response.json()["min_notice_minutes"] == 120

    def test_invalid_rules_are_rejected(self):
        link_id = self._create().json()["id"]
        response = self.client.patch(
            f"{LIST_URL}{link_id}/", {"duration_minutes": 0}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_is_soft_and_frees_the_slug(self):
        link_id = self._create().json()["id"]
        assert (
            self.client.delete(f"{LIST_URL}{link_id}/").status_code
            == status.HTTP_204_NO_CONTENT
        )
        assert BookingLink.objects.get(id=link_id).is_deleted is True
        # The partial unique constraint excludes deleted rows, so the slug is reusable.
        assert self._create().status_code == status.HTTP_201_CREATED

    def test_requires_authentication(self):
        anonymous = APIClient()
        response = anonymous.get(LIST_URL)
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_timezone_defaults_from_the_owners_calendar_settings(self):
        # The form no longer asks for a timezone: a wrong answer silently shifts
        # every offered slot, and the app already knows the owner's zone.
        CalendarSettings.objects.create(
            organization=self.org, user=self.user, timezone="Australia/Sydney"
        )
        response = self._create()
        assert response.status_code == status.HTTP_201_CREATED
        assert BookingLink.objects.get(slug="intro-call").timezone == "Australia/Sydney"

    def test_timezone_falls_back_to_utc_without_settings(self):
        response = self._create()
        assert response.status_code == status.HTTP_201_CREATED
        assert BookingLink.objects.get(slug="intro-call").timezone == "UTC"

    def test_an_explicit_timezone_is_still_honoured(self):
        response = self._create(timezone="Europe/London")
        assert response.status_code == status.HTTP_201_CREATED
        assert BookingLink.objects.get(slug="intro-call").timezone == "Europe/London"


class BookingLinkHostTests(TestCase):
    """
    Setting up a link for a colleague.

    Publishing someone else's availability is gated on a shared project —
    organisation membership alone is not enough, or anyone could expose
    anyone else's diary — and further limited to project owners/admins.
    """

    def setUp(self):
        self.org = Organization.objects.create(name="Host Org", slug="host-org")
        self.creator = User.objects.create_user(
            username="creator", email="creator@host.com", password="x",
            organization=self.org,
        )
        self.boss = User.objects.create_user(
            username="boss", email="boss@host.com", password="x",
            organization=self.org,
        )
        self.outsider = User.objects.create_user(
            username="outsider", email="outsider@host.com", password="x",
            organization=self.org,
        )

        self.project = Project.objects.create(
            name="Shared Project", organization=self.org, owner=self.creator
        )
        for user in (self.creator, self.boss):
            ProjectMember.objects.create(
                project=self.project, user=user, role="member", is_active=True
            )

        self.project_calendar = Calendar.objects.create(
            organization=self.org, owner=self.creator, project=self.project,
            name="Shared Project Calendar", timezone="UTC",
        )
        self.personal_calendar = Calendar.objects.create(
            organization=self.org, owner=self.creator,
            name="Personal", timezone="UTC",
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.creator)

    def _create(self, **overrides):
        payload = {
            "slug": "with-the-boss",
            "title": "With the boss",
            "calendar_id": str(self.project_calendar.id),
            "duration_minutes": 30,
        }
        payload.update(overrides)
        return self.client.post(LIST_URL, payload, format="json")

    def test_omitting_a_host_books_your_own_time(self):
        # Explicit null, which is what an untouched picker posts.
        response = self._create(host_id=None)
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        link = BookingLink.objects.get(slug="with-the-boss")
        assert link.owner == self.creator
        assert link.created_by == self.creator
        # Nothing to say when you set up your own link.
        assert response.json()["created_by_name"] == ""

    def test_can_set_up_a_link_for_someone_in_the_same_project(self):
        response = self._create(host_id=self.boss.id)
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        link = BookingLink.objects.get(slug="with-the-boss")
        assert link.owner == self.boss
        assert link.created_by == self.creator
        assert response.json()["host"]["id"] == self.boss.id
        assert response.json()["created_by_name"] == self.creator.get_username()

    def test_cannot_publish_the_time_of_someone_outside_the_project(self):
        response = self._create(host_id=self.outsider.id)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "host_id" in error_fields(response)
        assert not BookingLink.objects.filter(slug="with-the-boss").exists()

    def test_a_regular_member_cannot_publish_a_colleagues_time(self):
        self.client.force_authenticate(user=self.boss)
        response = self._create(host_id=self.creator.id)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "host_id" in error_fields(response)
        assert not BookingLink.objects.filter(slug="with-the-boss").exists()

    def test_a_regular_member_can_still_book_their_own_time(self):
        self.client.force_authenticate(user=self.boss)
        response = self._create(host_id=None)
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        link = BookingLink.objects.get(slug="with-the-boss")
        assert link.owner == self.boss
        assert link.created_by == self.boss

    def test_a_project_admin_can_publish_a_colleagues_time(self):
        admin = User.objects.create_user(
            username="projadmin", email="admin@host.com", password="x",
            organization=self.org,
        )
        ProjectMember.objects.create(
            project=self.project, user=admin, role="Organization Admin", is_active=True
        )
        self.client.force_authenticate(user=admin)
        response = self._create(host_id=self.boss.id)
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        link = BookingLink.objects.get(slug="with-the-boss")
        assert link.owner == self.boss
        assert link.created_by == admin

    def test_a_personal_calendar_cannot_host_someone_else(self):
        # No project on the calendar means no shared project to check against.
        response = self._create(
            host_id=self.boss.id, calendar_id=str(self.personal_calendar.id)
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "host_id" in error_fields(response)

    def test_an_unknown_host_is_rejected(self):
        response = self._create(host_id=999999)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "host_id" in error_fields(response)

    def test_the_timezone_defaults_to_the_hosts_zone_not_the_creators(self):
        CalendarSettings.objects.create(
            user=self.creator, organization=self.org, timezone="America/New_York"
        )
        CalendarSettings.objects.create(
            user=self.boss, organization=self.org, timezone="Asia/Tokyo"
        )

        response = self._create(host_id=self.boss.id)
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        # The windows describe the host's working day, so the creator's zone
        # would shift every offered slot.
        assert BookingLink.objects.get(slug="with-the-boss").timezone == "Asia/Tokyo"

    def test_both_the_host_and_the_creator_can_see_the_link(self):
        assert self._create(host_id=self.boss.id).status_code == status.HTTP_201_CREATED

        for user in (self.creator, self.boss):
            client = APIClient()
            client.force_authenticate(user=user)
            listed = client.get(LIST_URL)
            assert listed.status_code == status.HTTP_200_OK
            assert [row["slug"] for row in listed.json()] == ["with-the-boss"]

    def test_several_guests_can_be_named_at_once(self):
        response = self._create(
            host_id=self.boss.id, invitee_ids=[self.boss.id, self.creator.id]
        )
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        link = BookingLink.objects.get(slug="with-the-boss")
        assert set(link.invitee_users.values_list("pk", flat=True)) == {
            self.boss.id,
            self.creator.id,
        }

    def test_named_guests_and_plain_addresses_can_be_mixed(self):
        response = self._create(
            invitee_ids=[self.boss.id],
            invitee_emails=["stranger@example.com", "Other@Example.com"],
        )
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        link = BookingLink.objects.get(slug="with-the-boss")
        assert list(link.invitee_users.values_list("pk", flat=True)) == [self.boss.id]
        # Lower-cased so the same guest cannot be listed twice by casing alone.
        assert link.invitee_emails == ["stranger@example.com", "other@example.com"]

    def test_a_csm_customer_on_the_project_can_be_named(self):
        # Ray's case: someone in CSM attached to this project is "already in the
        # system", even though they are not a project member.
        client_user = User.objects.create_user(
            username="csmclient", email="client@acme-customer.com", password="x",
            organization=self.org,
        )
        Customer.objects.create(
            user=client_user,
            email=client_user.email,
            full_name="Acme Client",
            project=self.project,
        )
        response = self._create(invitee_ids=[client_user.id])
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        link = BookingLink.objects.get(slug="with-the-boss")
        assert list(link.invitee_users.values_list("pk", flat=True)) == [client_user.id]

    def test_a_personal_link_can_still_name_guests(self):
        response = self._create(
            calendar_id=str(self.personal_calendar.id),
            invitee_ids=[self.boss.id],
            invitee_emails=["guest@example.com"],
        )
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        link = BookingLink.objects.get(slug="with-the-boss")
        assert list(link.invitee_users.values_list("pk", flat=True)) == [self.boss.id]
        assert link.invitee_emails == ["guest@example.com"]

    def test_a_csm_customer_on_another_project_cannot_be_named(self):
        other_project = Project.objects.create(
            name="Other Project", organization=self.org, owner=self.creator
        )
        client_user = User.objects.create_user(
            username="othersclient", email="other@acme-customer.com", password="x",
            organization=self.org,
        )
        Customer.objects.create(
            user=client_user,
            email=client_user.email,
            full_name="Other Client",
            project=other_project,
        )
        response = self._create(invitee_ids=[client_user.id])
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "invitee_ids" in error_fields(response)

    def test_an_unrelated_colleague_still_sees_nothing(self):
        assert self._create(host_id=self.boss.id).status_code == status.HTTP_201_CREATED

        client = APIClient()
        client.force_authenticate(user=self.outsider)
        assert client.get(LIST_URL).json() == []

    def test_invitees_only_requires_someone_to_be_named(self):
        response = self._create(invitees_only=True)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "invitees_only" in error_fields(response)

    def test_invitees_only_can_be_set_when_people_are_named(self):
        response = self._create(
            invitees_only=True,
            invitee_ids=[self.creator.id],
        )
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        link = BookingLink.objects.get(slug="with-the-boss")
        assert link.invitees_only is True
        assert list(link.invitee_users.values_list("pk", flat=True)) == [self.creator.id]

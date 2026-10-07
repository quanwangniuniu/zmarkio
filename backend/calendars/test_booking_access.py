from django.contrib.auth import get_user_model
from django.test import TestCase

from calendars.booking_access import (
    booker_shares_project,
    can_book_public_link,
    has_named_invitees,
    is_named_invitee,
)
from calendars.models import BookingLink, Calendar
from core.models import Organization, Project, ProjectMember

User = get_user_model()


class BookingAccessTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", slug="access-acme")
        self.host = User.objects.create_user(
            username="host",
            email="host@acme.com",
            password="x",
            organization=self.org,
        )
        self.invitee = User.objects.create_user(
            username="invitee",
            email="invitee@acme.com",
            password="x",
            organization=self.org,
        )
        self.outsider = User.objects.create_user(
            username="outsider",
            email="outsider@acme.com",
            password="x",
            organization=self.org,
        )
        self.calendar = Calendar.objects.create(
            organization=self.org,
            owner=self.host,
            name="Primary",
            timezone="UTC",
            is_primary=True,
        )
        self.link = BookingLink.objects.create(
            organization=self.org,
            owner=self.host,
            calendar=self.calendar,
            slug="standup",
            title="Standup",
        )

    def test_open_link_lets_anyone_book(self):
        assert can_book_public_link(self.link, None)
        assert can_book_public_link(self.link, self.outsider)

    def test_invitees_only_requires_a_named_signed_in_person(self):
        self.link.invitees_only = True
        self.link.invitee_users.add(self.invitee)
        self.link.invitee_emails = ["guest@example.com"]
        self.link.save(update_fields=["invitees_only", "invitee_emails"])

        assert has_named_invitees(self.link)
        assert not can_book_public_link(self.link, None)
        assert not can_book_public_link(self.link, self.outsider)
        assert not can_book_public_link(self.link, self.host)
        assert can_book_public_link(self.link, self.invitee)

    def test_email_invitee_matches_once_they_sign_in(self):
        self.link.invitees_only = True
        self.link.invitee_emails = ["outsider@acme.com"]
        self.link.save(update_fields=["invitees_only", "invitee_emails"])

        assert is_named_invitee(self.link, self.outsider)
        assert can_book_public_link(self.link, self.outsider)

    def test_same_project_is_only_true_for_a_signed_in_teammate(self):
        project = Project.objects.create(name="Harbor", organization=self.org)
        ProjectMember.objects.create(project=project, user=self.host, is_active=True)
        ProjectMember.objects.create(project=project, user=self.invitee, is_active=True)
        self.calendar.project = project
        self.calendar.save(update_fields=["project"])

        assert not booker_shares_project(self.link, None)
        assert not booker_shares_project(self.link, self.outsider)
        assert booker_shares_project(self.link, self.invitee)
        assert booker_shares_project(self.link, self.host)

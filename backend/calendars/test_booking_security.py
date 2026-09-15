"""Authorization and transaction regressions for booking links."""
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core import signing
from calendars.booking_tokens import make_cancel_token, SALT
from calendars.booking_write import cancel_booking_events, create_booking_events
from calendars.models import BookingLink, Event, EventAttendee
from calendars.test_public_booking import PublicBookingTestBase, in_org, next_weekday_at


class BookingSecurityTests(PublicBookingTestBase):
    def book(self, hour=10, email="guest@example.com"):
        return self.client.post(self.booking_url, {"name": "Guest", "email": email,
            "start": next_weekday_at(hour).isoformat()}, format="json")

    def test_anonymous_email_cannot_write_another_accounts_calendar(self):
        guest = get_user_model().objects.create_user(username="victim", email="guest@example.com", organization=self.org)
        self.assertEqual(self.book().status_code, 201)
        with in_org(self.org):
            self.assertFalse(EventAttendee.objects.filter(user=guest).exists())
            self.assertEqual(Event.objects.count(), 1)

    def test_token_cannot_be_used_on_another_link_sharing_the_calendar(self):
        token = self.book().json()["cancel_token"]
        with in_org(self.org):
            BookingLink.objects.create(organization=self.org, owner=self.user,
                calendar=self.calendar, slug="other", title="Other")
        self.assertEqual(self.client.post(f"/api/public/book/{self.org.slug}/other/cancel/", {"token": token}, format="json").status_code, 404)
        self.assertEqual(self.client.get(f"/api/public/book/{self.org.slug}/other/calendar.ics", {"token": token}).status_code, 404)
        with in_org(self.org):
            self.assertFalse(Event.objects.get().is_deleted)

    def test_non_booking_event_token_does_not_grant_access(self):
        with in_org(self.org):
            event = Event.objects.create(organization=self.org, calendar=self.calendar, created_by=self.user,
                title="Private", start_datetime=next_weekday_at(10), end_datetime=next_weekday_at(11))
        self.assertEqual(self.client.post(self.availability_url + "cancel/", {"token": make_cancel_token(event.pk)}, format="json").status_code, 404)

    def test_malformed_token_types_never_raise(self):
        for token in [[], {}, 123, True, signing.dumps("not-a-uuid", salt=SALT)]:
            self.assertEqual(self.client.post(self.availability_url + "cancel/", {"token": token}, format="json").status_code, 404)

    def test_feed_and_cancel_survive_link_rename_and_delete(self):
        token = self.book().json()["cancel_token"]
        with in_org(self.org):
            self.link.slug = "renamed"
            self.link.is_deleted = True
            self.link.save()
        self.assertEqual(self.client.get(self.availability_url + "calendar.ics", {"token": token}).status_code, 200)
        self.assertEqual(self.client.post(self.availability_url + "cancel/", {"token": token}, format="json").status_code, 200)

    def test_broker_failure_does_not_report_committed_booking_as_failed(self):
        with patch("calendars.views.send_booking_confirmation_task.delay", side_effect=RuntimeError("broker down")), patch("calendars.views.export_event_to_google_task.delay", side_effect=RuntimeError("broker down")):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.book()
        self.assertEqual(response.status_code, 201)
        with in_org(self.org):
            self.assertEqual(Event.objects.count(), 1)

    def test_cancellation_rolls_back_if_one_copy_fails(self):
        guest = get_user_model().objects.create_user(username="copyguest", email="copy@example.com", organization=self.org)
        with in_org(self.org):
            event, _ = create_booking_events(link=self.link, title="Copies", description="",
                start=next_weekday_at(10), end=next_weekday_at(11), guest_user=guest,
                guest_name="Guest", guest_email=guest.email)
            save = Event.save
            calls = 0
            def fail_second(instance, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("copy failed")
                return save(instance, *args, **kwargs)
            with patch.object(Event, "save", fail_second):
                with self.assertRaises(RuntimeError):
                    cancel_booking_events(event)
            self.assertEqual(Event.objects.filter(is_deleted=False, status="confirmed").count(), 2)

    def test_overlapping_rebook_replaces_only_own_booking(self):
        guest = get_user_model().objects.create_user(username="rebook", email="guest@example.com", organization=self.org)
        self.client.force_authenticate(user=guest)
        self.assertEqual(self.book().status_code, 201)
        with in_org(self.org):
            self.link.slot_increment_minutes = 15
            self.link.save()
        response = self.client.post(self.booking_url, {"start": (next_weekday_at(10)+timedelta(minutes=15)).isoformat()}, format="json")
        self.assertEqual(response.status_code, 201)
        with in_org(self.org):
            self.assertEqual(Event.objects.filter(is_deleted=False).count(), 2)
            self.assertEqual(Event.objects.filter(is_deleted=True).count(), 2)

    def test_failed_rebook_keeps_original_copies(self):
        guest = get_user_model().objects.create_user(username="rebookbad", email="guest@example.com", organization=self.org)
        self.client.force_authenticate(user=guest)
        self.assertEqual(self.book().status_code, 201)
        self.assertEqual(self.book(hour=23).status_code, 409)
        with in_org(self.org):
            self.assertEqual(Event.objects.filter(is_deleted=False).count(), 2)
            self.assertFalse(Event.objects.filter(is_deleted=True).exists())

    def test_remote_future_timestamp_cannot_crash_slot_math(self):
        response = self.client.post(self.booking_url, {"name": "Guest", "email": "guest@example.com",
            "start": "9999-12-31T23:59:00Z"}, format="json")
        self.assertEqual(response.status_code, 409)

    def test_inactive_host_does_not_expose_availability(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(self.availability_url).status_code, 404)
        self.assertEqual(self.book().status_code, 404)

    def test_host_email_cannot_create_duplicate_attendee_rows(self):
        self.assertEqual(self.book(email=self.user.email).status_code, 400)
        with in_org(self.org):
            self.assertFalse(Event.objects.exists())

    def test_subscription_token_has_no_cancellation_permission(self):
        from urllib.parse import unquote, urlsplit
        booked = self.book().json()
        path = urlsplit(booked["feed_url"]).path
        feed_token = unquote(path.rsplit("/", 1)[1][:-4])
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.post(self.availability_url + "cancel/", {"token": feed_token}, format="json").status_code, 404)
        self.assertEqual(self.client.post(self.availability_url + "cancel/", {"token": booked["cancel_token"]}, format="json").status_code, 200)

    def test_recovery_broker_failure_reports_unavailable_without_leaking_bookings(self):
        with patch("calendars.views.send_booking_recovery_task.delay", side_effect=RuntimeError("broker down")):
            response = self.client.post(self.availability_url + "lookup/", {"email": "guest@example.com"}, format="json")
        self.assertEqual(response.status_code, 503)

    def test_long_valid_names_do_not_overflow_event_title(self):
        with in_org(self.org):
            self.link.title = "T" * 255
            self.link.save()
        response = self.client.post(self.booking_url, {"name": "N" * 255, "email": "guest@example.com",
            "start": next_weekday_at(10).isoformat()}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.json()["title"]), 255)

    def test_member_duplicate_submit_does_not_cancel_and_recreate_booking(self):
        guest = get_user_model().objects.create_user(username="duplicate", email="guest@example.com", organization=self.org)
        self.client.force_authenticate(user=guest)
        self.assertEqual(self.book().status_code, 201)
        self.assertEqual(self.book().status_code, 409)
        with in_org(self.org):
            self.assertEqual(Event.objects.filter(is_deleted=False).count(), 2)
            self.assertFalse(Event.objects.filter(is_deleted=True).exists())

"""Real PostgreSQL transactions: concurrent links must share a calendar lock."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, connections
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from calendars.models import BookingLink, Calendar, Event
from calendars.test_public_booking import WEEKDAY_WINDOWS, in_org, next_weekday_at
from core.models import Organization
from core.services.tenant import slug_to_schema_name


class ConcurrentBookingTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name="Concurrent Booking", slug="booking-race")
        self.owner = get_user_model().objects.create_user(
            username="race-owner", email="race@example.com", organization=self.org
        )
        with in_org(self.org):
            self.calendar = Calendar.objects.create(
                organization=self.org, owner=self.owner, name="Race diary", timezone="UTC"
            )
            self.links = [BookingLink.objects.create(
                organization=self.org, owner=self.owner, calendar=self.calendar,
                slug=f"race-{i}", title="Concurrent booking", duration_minutes=60,
                slot_increment_minutes=15, min_notice_minutes=0, timezone="UTC",
                availability_windows=WEEKDAY_WINDOWS,
            ) for i in range(2)]

    def tearDown(self):
        # TransactionTestCase flushes public tables; remove our committed tenant
        # too, so reruns cannot reuse tenant rows pointing at flushed users.
        with connection.cursor() as cursor:
            cursor.execute('DROP SCHEMA IF EXISTS "org_booking_race" CASCADE')
        super().tearDown()

    def _race(self, different_links=False):
        barrier = Barrier(2)
        start = next_weekday_at(11)

        def submit(index):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                link = self.links[index if different_links else 0]
                response = APIClient().post(
                    f"/api/public/book/{self.org.slug}/{link.slug}/bookings/",
                    {"name": f"Guest {index}", "email": f"guest{index}@example.com",
                     "start": start.isoformat()}, format="json",
                )
                return response.status_code
            finally:
                connections.close_all()

        # External delivery is not part of locking. Requests, tenant lookup,
        # availability queries and all event writes use real DB connections.
        with patch("calendars.views.export_event_to_google_task.delay"), patch(
            "calendars.views.send_booking_confirmation_task.delay"
        ), patch("calendars.views.notify_booking_made"), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertEqual(sorted(results), [201, 409])
        with in_org(self.org):
            self.assertEqual(Event.objects.filter(calendar=self.calendar, is_deleted=False).count(), 1)

    def test_same_link_concurrent_posts_create_one_event(self):
        self._race()

    def test_different_links_to_same_calendar_create_one_event(self):
        self._race(different_links=True)

    def test_simultaneous_cancellation_notifies_once(self):
        from calendars.booking_tokens import make_cancel_token
        from calendars.booking_write import create_booking_events
        with in_org(self.org):
            event, _ = create_booking_events(link=self.links[0], title="Cancel race", description="",
                start=next_weekday_at(10), end=next_weekday_at(11), guest_user=None,
                guest_name="Guest", guest_email="guest@example.com")
        barrier = Barrier(2)
        token = make_cancel_token(event.pk)
        def cancel(_):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                return APIClient().post(f"/api/public/book/{self.org.slug}/{self.links[0].slug}/cancel/",
                    {"token": token}, format="json").status_code
            finally:
                connections.close_all()
        with patch("calendars.views.export_event_to_google_task.delay"), patch(
            "calendars.views.notify_booking_cancelled"
        ) as notice, ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(cancel, range(2))), [200, 200])
            self.assertEqual(notice.call_count, 1)
        with in_org(self.org):
            event.refresh_from_db()
            self.assertTrue(event.is_deleted)

    def test_reciprocal_member_bookings_do_not_deadlock_or_double_book(self):
        other = get_user_model().objects.create_user(username="other-host", email="other@example.com", organization=self.org)
        with in_org(self.org):
            self.calendar.is_primary = True
            self.calendar.save()
            other_calendar = Calendar.objects.create(organization=self.org, owner=other,
                name="Other diary", timezone="UTC", is_primary=True)
            other_link = BookingLink.objects.create(organization=self.org, owner=other,
                calendar=other_calendar, slug="other-host", title="Other host",
                duration_minutes=60, slot_increment_minutes=15, min_notice_minutes=0,
                timezone="UTC", availability_windows=WEEKDAY_WINDOWS)
        barrier = Barrier(2)
        pairs = [(self.owner, other_link), (other, self.links[0])]
        def book(pair):
            user, link = pair
            connections.close_all()
            try:
                client = APIClient()
                client.force_authenticate(user=user)
                barrier.wait(timeout=10)
                return client.post(f"/api/public/book/{self.org.slug}/{link.slug}/bookings/",
                    {"start": next_weekday_at(11).isoformat()}, format="json").status_code
            finally:
                connections.close_all()
        with patch("calendars.views.export_event_to_google_task.delay"), patch(
            "calendars.views.send_booking_confirmation_task.delay"
        ), patch("calendars.views.notify_booking_made"), ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(book, pairs)), [201, 409])
        with in_org(self.org):
            self.assertEqual(Event.objects.filter(is_deleted=False).count(), 2)

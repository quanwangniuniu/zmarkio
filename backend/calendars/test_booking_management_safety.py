"""Calendar read access cannot be escalated into public booking write access."""
from types import SimpleNamespace
from django.test import TestCase
from calendars.models import CalendarShare
from calendars.serializers import EventCreateUpdateSerializer
from calendars.test_booking_link_crud import BookingLinkCrudTests


class BookingManagementSafetyTests(BookingLinkCrudTests):
    def test_read_only_calendar_cannot_receive_public_bookings(self):
        CalendarShare.objects.create(organization=self.org, calendar=self.colleague_calendar,
            shared_with=self.user, permission="view_all")
        response = self._create(calendar_id=str(self.colleague_calendar.pk))
        self.assertEqual(response.status_code, 400)

    def test_invalid_rules_return_validation_error(self):
        for fields in [dict(timezone="Not/AZone"), dict(availability_windows={"weekday": 0}),
            dict(availability_windows=[{"weekday": 0, "start": "bad", "end": "10:00"}]),
            dict(invitee_emails=["not an email"]), dict(invitee_emails="a@example.com"),
            dict(duration_minutes=999999999), dict(max_advance_days=999999999),
            dict(buffer_before_minutes=-1)]:
            self.assertEqual(self._create(**fields).status_code, 400, fields)


class BookingMetadataTests(TestCase):
    def test_caller_cannot_join_another_booking_group(self):
        serializer = EventCreateUpdateSerializer(data={"metadata": {"source": "booking_link", "booking_group": "another"}}, partial=True)
        self.assertFalse(serializer.is_valid())
        self.assertIn("metadata", serializer.errors)

    def test_edit_preserves_server_booking_identity(self):
        serializer = EventCreateUpdateSerializer()
        serializer.instance = SimpleNamespace(metadata={"source": "booking_link", "booking_group": "original"})
        self.assertEqual(serializer.validate_metadata({"note": "ok"}), {"source": "booking_link", "booking_group": "original", "note": "ok"})

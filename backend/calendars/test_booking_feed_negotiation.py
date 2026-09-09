"""Native calendar clients must reach the ICS handler with their Accept header."""

from unittest.mock import patch

import pytest
from django.http import HttpResponse
from rest_framework.test import APIRequestFactory

from calendars.views import PublicBookingFeedView


@pytest.mark.parametrize("accept", ["text/calendar", "text/calendar, */*;q=0.1", "*/*"])
def test_calendar_accept_header_reaches_the_feed_handler(accept):
    request = APIRequestFactory().get("/booking.ics", HTTP_ACCEPT=accept)
    response = HttpResponse("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", content_type="text/calendar")
    with patch.object(PublicBookingFeedView, "get", return_value=response) as handler, patch.object(
        PublicBookingFeedView, "get_throttles", return_value=[]
    ):
        result = PublicBookingFeedView.as_view()(request, org_slug="qa", link_slug="intro")
    handler.assert_called_once()
    assert result.status_code == 200
    assert result["Content-Type"].startswith("text/calendar")
    assert result.content.startswith(b"BEGIN:VCALENDAR")


def test_unavailable_calendar_still_returns_404_for_calendar_clients():
    request = APIRequestFactory().get("/booking.ics", HTTP_ACCEPT="text/calendar")
    with patch("calendars.views._resolve_booking_org", return_value=None), patch.object(
        PublicBookingFeedView, "get_throttles", return_value=[]
    ):
        result = PublicBookingFeedView.as_view()(request, org_slug="missing", link_slug="intro")
    assert result.status_code == 404

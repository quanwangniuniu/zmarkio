"""
The iCalendar text itself.

Worth testing without a request because the format is unforgiving: strict
parsers reject over-length lines, and a mis-escaped separator silently
truncates a field.
"""

from datetime import datetime, timedelta, timezone as dt_timezone

from django.test import TestCase

from calendars.booking_ics import as_webcal_url, build_booking_ics, escape_text, fold_line

START = datetime(2027, 3, 2, 9, 0, tzinfo=dt_timezone.utc)
END = START + timedelta(hours=1)


def build(**overrides) -> str:
    payload = {
        "uid": "abc@marketing-simplified",
        "title": "Intro Call",
        "start": START,
        "end": END,
    }
    payload.update(overrides)
    return build_booking_ics(**payload)


class EscapingTests(TestCase):
    def test_reserved_characters_are_escaped(self):
        assert escape_text("a,b;c") == "a\\,b\\;c"

    def test_backslashes_go_first_so_later_escapes_are_not_doubled(self):
        assert escape_text("a\\b,c") == "a\\\\b\\,c"

    def test_newlines_become_literal_escapes(self):
        assert escape_text("one\r\ntwo\nthree") == "one\\ntwo\\nthree"


class FoldingTests(TestCase):
    def test_a_short_line_is_left_alone(self):
        assert fold_line("SUMMARY:hi") == ["SUMMARY:hi"]

    def test_a_long_line_is_wrapped_with_a_leading_space(self):
        folded = fold_line("SUMMARY:" + "x" * 200)
        assert len(folded) > 1
        assert all(line.startswith(" ") for line in folded[1:])

    def test_no_folded_line_exceeds_the_octet_limit(self):
        for line in fold_line("URL:https://example.com/" + "a" * 300):
            assert len(line.encode("utf-8")) <= 75

    def test_multibyte_characters_are_not_split_down_the_middle(self):
        # Splitting on bytes rather than characters would emit invalid UTF-8.
        folded = fold_line("SUMMARY:" + "会議" * 60)
        for line in folded:
            assert len(line.encode("utf-8")) <= 75
        assert "".join(f.lstrip(" ") if i else f for i, f in enumerate(folded)).endswith(
            "会議"
        )


class WebcalUrlTests(TestCase):
    def test_https_becomes_webcal(self):
        assert (
            as_webcal_url("https://app.example/api/public/book/acme/intro/abc.ics")
            == "webcal://app.example/api/public/book/acme/intro/abc.ics"
        )

    def test_http_becomes_webcal(self):
        # Localhost is http; the scheme still has to say "subscribe".
        assert as_webcal_url("http://localhost/feed.ics") == "webcal://localhost/feed.ics"

    def test_an_already_webcal_url_is_left_alone(self):
        assert as_webcal_url("webcal://app.example/feed.ics") == "webcal://app.example/feed.ics"


class BuildTests(TestCase):
    def test_it_emits_one_complete_vevent(self):
        ics = build()
        assert ics.startswith("BEGIN:VCALENDAR")
        assert ics.rstrip("\r\n").endswith("END:VCALENDAR")
        assert ics.endswith("\r\n")
        assert "X-WR-CALNAME:Intro Call" in ics
        assert ics.count("BEGIN:VEVENT") == 1
        assert "DTSTART:20270302T090000Z" in ics
        assert "DTEND:20270302T100000Z" in ics

    def test_lines_are_separated_by_crlf(self):
        assert "\r\n" in build()

    def test_a_live_booking_is_confirmed(self):
        assert "STATUS:CONFIRMED" in build()

    def test_a_cancelled_booking_is_published_as_cancelled(self):
        # Dropping the event instead would tell a subscriber nothing; the entry
        # has to be superseded so their calendar removes it.
        ics = build(cancelled=True)
        assert "STATUS:CANCELLED" in ics
        assert "SEQUENCE:1" in ics

    def test_the_uid_is_stable_so_subscribers_update_rather_than_duplicate(self):
        assert "UID:abc@marketing-simplified" in build()
        assert "UID:abc@marketing-simplified" in build(cancelled=True)

    def test_optional_fields_are_omitted_when_empty(self):
        ics = build()
        assert "DESCRIPTION:" not in ics
        assert "URL:" not in ics
        assert "ORGANIZER:" not in ics

    def test_a_naive_free_datetime_is_normalised_to_utc(self):
        ics = build(start=datetime(2027, 3, 2, 11, 0, tzinfo=dt_timezone(timedelta(hours=2))))
        assert "DTSTART:20270302T090000Z" in ics


def test_bare_carriage_return_is_escaped():
    from calendars.booking_ics import escape_text
    assert escape_text("Guest\rATTENDEE:intruder@example.com") == "Guest\\nATTENDEE:intruder@example.com"

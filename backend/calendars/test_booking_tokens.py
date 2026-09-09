"""Cancellation tokens - the only thing standing between a guest and someone else's booking."""

import uuid

from django.core import signing
from django.test import TestCase

from calendars.booking_tokens import SALT, make_cancel_token, read_cancel_token


class BookingTokenTests(TestCase):
    def test_a_token_round_trips_to_its_event(self):
        event_id = uuid.uuid4()
        assert read_cancel_token(make_cancel_token(event_id)) == str(event_id)

    def test_a_tampered_token_is_refused(self):
        token = make_cancel_token(uuid.uuid4())
        assert read_cancel_token(token[:-1] + ("x" if token[-1] != "x" else "y")) is None

    def test_rubbish_is_refused_rather_than_raising(self):
        assert read_cancel_token("not-a-token") is None
        assert read_cancel_token("") is None

    def test_a_token_signed_for_something_else_is_refused(self):
        # Same secret, different purpose: the salt has to keep them apart, or a
        # token minted elsewhere in the app would cancel bookings.
        foreign = signing.dumps(str(uuid.uuid4()), salt="some.other.purpose")
        assert read_cancel_token(foreign) is None

    def test_an_expired_token_is_refused(self):
        token = make_cancel_token(uuid.uuid4())
        with self.settings():
            assert signing.loads(token, salt=SALT, max_age=10) is not None
        # Age it past the window by asking for an impossible max_age.
        try:
            signing.loads(token, salt=SALT, max_age=-1)
            raised = False
        except signing.SignatureExpired:
            raised = True
        assert raised

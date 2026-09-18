from django.test import TestCase

from calendars.booking_lookup import phones_match


class PhoneMatchTests(TestCase):
    def test_spaces_and_country_code_still_match(self):
        assert phones_match("+44 7700 900123", "447700900123")

    def test_unrelated_numbers_do_not_match(self):
        assert not phones_match("+44 7700 900123", "447700900999")

"""MED-506: SSRF guard for user-supplied external URLs."""
from django.test import SimpleTestCase

from ad_copy_variation.ssrf_guard import (
    UnsafeUrlError,
    assert_public_url,
    is_private_address,
)

PRIVATE_IPS = [
    "0.0.0.0",
    "10.1.2.3",
    "100.64.0.1",
    "127.0.0.1",
    "169.254.169.254",
    "172.16.0.1",
    "192.168.1.1",
    "224.0.0.1",
    "::",
    "::1",
    "fc00::1",
    "fe80::1",
    "ff02::1",
    "::ffff:127.0.0.1",
    "::ffff:10.0.0.1",
    "not-an-ip",
]

PUBLIC_IPS = [
    "8.8.8.8",
    "1.1.1.1",
    "93.184.216.34",
    "2606:4700:4700::1111",
]


def _resolver(*ips):
    """Fake DNS: always answers with the given IPs."""
    return lambda host: list(ips)


def _no_dns(host):
    raise AssertionError("DNS must not be called for an IP literal")


def _dns_down(host):
    raise OSError("Name or service not known")


class IsPrivateAddressTests(SimpleTestCase):
    def test_private_addresses_are_blocked(self):
        for ip in PRIVATE_IPS:
            with self.subTest(ip=ip):
                self.assertTrue(is_private_address(ip))

    def test_public_addresses_are_allowed(self):
        for ip in PUBLIC_IPS:
            with self.subTest(ip=ip):
                self.assertFalse(is_private_address(ip))


class AssertPublicUrlTests(SimpleTestCase):
    def test_accepts_hostname_resolving_to_public_ip(self):
        assert_public_url("https://example.com/ad", resolver=_resolver("93.184.216.34"))

    def test_rejects_loopback_literal_without_dns(self):
        with self.assertRaises(UnsafeUrlError):
            assert_public_url("http://127.0.0.1:8000/admin", resolver=_no_dns)

    def test_rejects_ipv6_loopback_literal(self):
        with self.assertRaises(UnsafeUrlError):
            assert_public_url("http://[::1]/", resolver=_no_dns)

    def test_rejects_cloud_metadata_address(self):
        with self.assertRaises(UnsafeUrlError):
            assert_public_url("http://169.254.169.254/latest/meta-data/", resolver=_no_dns)

    def test_rejects_hostname_resolving_to_private_ip(self):
        with self.assertRaises(UnsafeUrlError):
            assert_public_url("https://evil.example/", resolver=_resolver("10.0.0.5"))

    def test_rejects_when_any_resolved_ip_is_private(self):
        with self.assertRaises(UnsafeUrlError):
            assert_public_url(
                "https://mixed.example/",
                resolver=_resolver("93.184.216.34", "127.0.0.1"),
            )

    def test_rejects_when_host_cannot_be_resolved(self):
        with self.assertRaises(UnsafeUrlError):
            assert_public_url("https://nope.example/", resolver=_dns_down)

    def test_rejects_url_without_host(self):
        with self.assertRaises(UnsafeUrlError):
            assert_public_url("https:///no-host", resolver=_no_dns)

    def test_rejects_decimal_encoded_loopback_with_real_resolver(self):
        # 2130706433 == 127.0.0.1; getaddrinfo decodes it locally, no network needed.
        with self.assertRaises(UnsafeUrlError):
            assert_public_url("http://2130706433/")

    def test_error_is_a_value_error_so_views_return_400(self):
        self.assertTrue(issubclass(UnsafeUrlError, ValueError))

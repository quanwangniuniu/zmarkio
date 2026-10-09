"""SSRF guard + URL extraction for chat link previews (MED-279, ticket 01).

Pure unit tests — no DB, no network. DNS and HTTP are both mocked so the suite is
deterministic offline and can never actually reach an internal address.
"""
import ipaddress
import socket
from unittest.mock import patch

import pytest

from chat.services import (
    LINK_PREVIEW_MAX_BYTES,
    LINK_PREVIEW_MAX_REDIRECTS,
    UnsafeUrlError,
    extract_first_url,
    fetch_url_safely,
    normalize_preview_url,
    resolve_public_url,
    validate_public_url,
)

PUBLIC_IP = '93.184.216.34'


def fake_getaddrinfo(mapping=None):
    """socket.getaddrinfo stand-in: IP literals resolve to themselves, hostnames
    resolve via `mapping` so a test can point a public-looking domain anywhere."""
    hosts = mapping or {}

    def _resolve(host, port=None, *args, **kwargs):
        try:
            ipaddress.ip_address(host)
            ips = [host]
        except ValueError:
            ips = hosts.get(host)
            if ips is None:
                raise socket.gaierror(f'unknown host: {host}')
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, port or 0))
            for ip in ips
        ]

    return _resolve


class FakeResponse:
    """Minimal requests.Response stand-in for the guarded fetch loop."""

    def __init__(self, status_code=200, headers=None, body='', url=''):
        self.status_code = status_code
        self.headers = headers or {'Content-Type': 'text/html; charset=utf-8'}
        self.text = body
        self.content = body.encode()
        self.url = url

    def iter_content(self, chunk_size=8192):
        data = self.content
        for start in range(0, len(data), chunk_size):
            yield data[start:start + chunk_size]

    def close(self):
        pass


class TestNormalizePreviewUrl:
    def test_strips_fragment(self):
        assert normalize_preview_url('https://example.com/a#section') == 'https://example.com/a'

    def test_lowercases_host_only(self):
        # host is case-insensitive; the path is NOT — it must survive untouched
        assert normalize_preview_url('https://EXAMPLE.com/Path') == 'https://example.com/Path'

    def test_same_link_written_differently_maps_to_one_key(self):
        a = normalize_preview_url('https://Example.com/story#top')
        b = normalize_preview_url('https://example.com/story')
        assert a == b


class TestExtractFirstUrl:
    def test_finds_the_only_url(self):
        assert extract_first_url('look at https://example.com/a please') == 'https://example.com/a'

    def test_returns_the_first_of_many(self):
        text = 'https://first.example.com and https://second.example.com'
        assert extract_first_url(text) == 'https://first.example.com'

    @pytest.mark.parametrize('text', ['no link here', '', None])
    def test_returns_none_without_a_url(self, text):
        assert extract_first_url(text) is None


class TestSchemeAllowList:
    @pytest.mark.parametrize('url', [
        'file:///etc/passwd',
        'ftp://example.com/x',
        'gopher://example.com/x',
        'javascript:alert(1)',
        'data:text/html,<h1>x</h1>',
        'not a url',
        '',
        None,
    ])
    def test_rejects_non_http_schemes(self, url):
        with patch('chat.services.socket.getaddrinfo', fake_getaddrinfo()):
            with pytest.raises(UnsafeUrlError):
                validate_public_url(url)


class TestBlocksInternalAddresses:
    @pytest.mark.parametrize('url', [
        'http://127.0.0.1/x',            # loopback
        'http://[::1]/x',                # IPv6 loopback
        'http://10.0.0.1/x',             # private class A
        'http://172.16.0.1/x',           # private class B
        'http://192.168.1.1/x',          # private class C
        'http://169.254.169.254/latest/meta-data/',  # cloud metadata
        'http://0.0.0.0/x',              # unspecified
    ])
    def test_rejects_internal_ip_literals(self, url):
        with patch('chat.services.socket.getaddrinfo', fake_getaddrinfo()):
            with pytest.raises(UnsafeUrlError):
                validate_public_url(url)

    def test_rejects_localhost_hostname(self):
        resolver = fake_getaddrinfo({'localhost': ['127.0.0.1']})
        with patch('chat.services.socket.getaddrinfo', resolver):
            with pytest.raises(UnsafeUrlError):
                validate_public_url('http://localhost:8000/admin')

    def test_rejects_public_domain_that_resolves_to_private_ip(self):
        """The attack a string blacklist cannot see: an ordinary-looking domain
        whose DNS points at loopback."""
        resolver = fake_getaddrinfo({'evil.example.com': ['127.0.0.1']})
        with patch('chat.services.socket.getaddrinfo', resolver):
            with pytest.raises(UnsafeUrlError):
                validate_public_url('https://evil.example.com/looks-fine')

    def test_rejects_when_any_resolved_ip_is_private(self):
        """Multi-record DNS: one public answer must not launder a private one."""
        resolver = fake_getaddrinfo({'mixed.example.com': [PUBLIC_IP, '10.0.0.5']})
        with patch('chat.services.socket.getaddrinfo', resolver):
            with pytest.raises(UnsafeUrlError):
                validate_public_url('https://mixed.example.com/x')

    def test_rejects_unresolvable_host(self):
        with patch('chat.services.socket.getaddrinfo', fake_getaddrinfo()):
            with pytest.raises(UnsafeUrlError):
                validate_public_url('https://nope.example.com/x')


class TestAcceptsPublicUrls:
    def test_public_url_passes_and_comes_back_normalized(self):
        resolver = fake_getaddrinfo({'example.com': [PUBLIC_IP]})
        with patch('chat.services.socket.getaddrinfo', resolver):
            assert validate_public_url('https://EXAMPLE.com/story#top') == 'https://example.com/story'


class TestGuardedFetch:
    def _resolver(self):
        return fake_getaddrinfo({
            'example.com': [PUBLIC_IP],
            'redirector.example.com': [PUBLIC_IP],
        })

    def test_returns_html_for_a_public_page(self):
        html = '<html><head><title>hi</title></head></html>'
        with patch('chat.services.socket.getaddrinfo', self._resolver()), \
             patch('chat.services.requests.get', return_value=FakeResponse(body=html)):
            assert fetch_url_safely('https://example.com/a') == html

    def test_rejects_redirect_to_internal_address(self):
        """A public URL that 302s to cloud metadata must be caught at the hop."""
        hop = FakeResponse(
            status_code=302,
            headers={'Location': 'http://169.254.169.254/latest/meta-data/'},
        )
        with patch('chat.services.socket.getaddrinfo', self._resolver()), \
             patch('chat.services.requests.get', return_value=hop):
            with pytest.raises(UnsafeUrlError):
                fetch_url_safely('https://redirector.example.com/go')

    def test_rejects_too_many_redirects(self):
        loop = FakeResponse(status_code=302, headers={'Location': 'https://example.com/again'})
        with patch('chat.services.socket.getaddrinfo', self._resolver()), \
             patch('chat.services.requests.get', return_value=loop) as mock_get:
            with pytest.raises(UnsafeUrlError):
                fetch_url_safely('https://example.com/start')
        assert mock_get.call_count <= LINK_PREVIEW_MAX_REDIRECTS + 1

    def test_rejects_non_html_content_type(self):
        binary = FakeResponse(headers={'Content-Type': 'application/zip'}, body='PK')
        with patch('chat.services.socket.getaddrinfo', self._resolver()), \
             patch('chat.services.requests.get', return_value=binary):
            with pytest.raises(UnsafeUrlError):
                fetch_url_safely('https://example.com/big.zip')

    def test_stops_reading_at_the_size_cap(self):
        oversized = FakeResponse(body='x' * (LINK_PREVIEW_MAX_BYTES + 5000))
        with patch('chat.services.socket.getaddrinfo', self._resolver()), \
             patch('chat.services.requests.get', return_value=oversized):
            body = fetch_url_safely('https://example.com/huge')
        assert len(body.encode()) <= LINK_PREVIEW_MAX_BYTES

    def test_passes_a_timeout_to_the_http_client(self):
        with patch('chat.services.socket.getaddrinfo', self._resolver()), \
             patch('chat.services.requests.get', return_value=FakeResponse(body='<html></html>')) as mock_get:
            fetch_url_safely('https://example.com/a')
        assert mock_get.call_args.kwargs.get('timeout') is not None
        # redirects must be handled by us, hop by hop — never by the HTTP client
        assert mock_get.call_args.kwargs.get('allow_redirects') is False


class TestPinnedResolution:
    """The address that passed validation is the address we connect to (MED-279).

    Validating a hostname and then letting the HTTP client resolve it again leaves a
    TOCTOU window: an attacker controlling DNS can answer publicly for the check and
    privately for the connection (DNS rebinding). Pinning closes it.
    """

    def test_validation_reports_the_address_it_approved(self):
        resolver = fake_getaddrinfo({'example.com': [PUBLIC_IP]})
        with patch('chat.services.socket.getaddrinfo', resolver):
            url, ip = resolve_public_url('https://EXAMPLE.com/story#top')

        assert url == 'https://example.com/story'
        assert ip == PUBLIC_IP

    def test_the_fetch_pins_the_validated_address(self):
        """The connection must be made to the checked IP, not to a fresh lookup."""
        resolver = fake_getaddrinfo({'example.com': [PUBLIC_IP]})
        with patch('chat.services.socket.getaddrinfo', resolver), \
             patch('chat.services.requests.get', return_value=FakeResponse(body='<html></html>')), \
             patch('chat.services._pinned_address') as pin:
            fetch_url_safely('https://example.com/a')

        pin.assert_called_once()
        assert pin.call_args.args[1] == PUBLIC_IP

    def test_each_redirect_hop_is_pinned_to_its_own_validated_address(self):
        hop = FakeResponse(status_code=302, headers={'Location': 'https://second.example.com/x'})
        final = FakeResponse(body='<html>done</html>')
        resolver = fake_getaddrinfo({
            'first.example.com': [PUBLIC_IP],
            'second.example.com': ['93.184.216.35'],
        })
        with patch('chat.services.socket.getaddrinfo', resolver), \
             patch('chat.services.requests.get', side_effect=[hop, final]), \
             patch('chat.services._pinned_address') as pin:
            fetch_url_safely('https://first.example.com/go')

        pinned = [call.args[1] for call in pin.call_args_list]
        assert pinned == [PUBLIC_IP, '93.184.216.35']

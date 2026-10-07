"""
Outbound POSTs to customer-supplied webhook URLs.

A webhook URL is an SSRF surface just like a chat link preview: the server would
POST wherever it points. The chat link-preview guard is reused, so a target is
judged by its resolved IP and the connection is pinned to the address that
passed the check. Redirects are never followed.
"""

from urllib.parse import urlparse

import requests
from django.conf import settings

from chat.services import UnsafeUrlError, _pinned_address, resolve_public_url

TIMEOUT_SECONDS = 10


def _private_hosts_allowed():
    # Development only: lets a demo receiver run on localhost or the compose network.
    return getattr(settings, 'PUBLIC_API_WEBHOOK_ALLOW_PRIVATE_HOSTS', False)


def validate_webhook_url(url):
    """Raise UnsafeUrlError unless the server may deliver to `url`."""
    scheme = urlparse(url).scheme.lower()
    if _private_hosts_allowed():
        if scheme not in ('http', 'https'):
            raise UnsafeUrlError('Webhook URLs must use http or https.')
        return
    if scheme != 'https':
        raise UnsafeUrlError('Webhook URLs must use https.')
    resolve_public_url(url)


def post_json_safely(url, body, headers):
    """POST `body` (bytes) to `url`; the caller must close the returned response."""
    options = {'data': body, 'headers': headers, 'timeout': TIMEOUT_SECONDS, 'allow_redirects': False, 'stream': True}
    if _private_hosts_allowed():
        return requests.post(url, **options)
    validate_webhook_url(url)
    normalized, address = resolve_public_url(url)
    with _pinned_address(urlparse(normalized).hostname, address):
        return requests.post(normalized, **options)

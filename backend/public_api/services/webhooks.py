"""
Outbound webhooks: endpoint secrets, signing, delivery, and turning ticket events into deliveries.

Signature: `X-Zmarkio-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>.<body>")>`,
where body is the exact request body. Receivers recompute it with their secret,
compare in constant time, and reject stale timestamps to stop replays.

A webhook URL is an SSRF surface like a chat link preview: the server POSTs
wherever it points. The chat link-preview guard is reused, so a target is judged
by its resolved IP, the connection is pinned to that address, and redirects are
never followed.
"""

import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from functools import partial
from urllib.parse import urlparse

import requests
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from chat.services import UnsafeUrlError, _pinned_address, resolve_public_url
from core.crypto import decrypt_token, encrypt_token
from public_api.models import WebhookEndpoint

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10


# ---------------------------------------------------------------------------
# Secrets, signing and delivery
# ---------------------------------------------------------------------------

def canonical_json(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), cls=DjangoJSONEncoder)


def sign(secret, timestamp, body):
    return hmac.new(secret.encode(), f'{timestamp}.'.encode() + body, hashlib.sha256).hexdigest()


def delivery_headers(endpoint, payload, body, attempt):
    timestamp = int(time.time())
    signature = sign(decrypt_token(endpoint.secret_encrypted), timestamp, body)
    return {
        'Content-Type': 'application/json',
        'User-Agent': 'Zmarkio-Webhooks/1',
        'X-Zmarkio-Signature': f't={timestamp},v1={signature}',
        'X-Zmarkio-Event': payload['type'],
        'X-Zmarkio-Delivery': payload['id'],
        'X-Zmarkio-Attempt': str(attempt),
    }


def validate_webhook_url(url):
    """Return (normalized url, vetted address); raise UnsafeUrlError unless the server may deliver to `url`."""
    if urlparse(url).scheme.lower() != 'https':
        raise UnsafeUrlError('Webhook URLs must use https.')
    return resolve_public_url(url)


def post_json_safely(url, body, headers):
    """POST `body` (bytes) to `url`; the caller must close the returned response."""
    normalized, address = validate_webhook_url(url)
    with _pinned_address(urlparse(normalized).hostname, address):
        return requests.post(
            normalized, data=body, headers=headers,
            timeout=TIMEOUT_SECONDS, allow_redirects=False, stream=True,
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def create_endpoint(*, organization_id, project_id, url, events, user, description='', is_active=True):
    """Return (endpoint, signing secret). The secret is not recoverable from the API afterwards."""
    secret = f'whsec_{secrets.token_urlsafe(32)}'
    endpoint = WebhookEndpoint.objects.create(
        organization_id=organization_id,
        project_id=project_id,
        url=url,
        description=description,
        events=events,
        is_active=is_active,
        secret_encrypted=encrypt_token(secret),
        created_by=user,
    )
    return endpoint, secret


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def build_event(event_type, *, organization_id, project_id, data):
    payload = {
        'id': str(uuid.uuid4()),
        'type': event_type,
        'created_at': timezone.now().isoformat(),
        'organization_id': organization_id,
        'project_id': project_id,
        'data': data,
    }
    # Round-trip so dates and decimals are plain JSON for Celery.
    return json.loads(canonical_json(payload))


def _enqueue(endpoint_ids, payload):
    from public_api.tasks import deliver_webhook

    for endpoint_id in endpoint_ids:
        # After commit: nothing is announced for a write that rolls back.
        transaction.on_commit(partial(deliver_webhook.delay, endpoint_id, payload))


def emit_ticket_event(event_type, ticket, **extra):
    """Queue `event_type` for every active endpoint of the ticket's workspace subscribed to it."""
    from public_api.serializers import PublicTicketSerializer

    queue = ticket.queue
    if queue is None or queue.organisation is None or queue.project_id is None:
        logger.debug('Ticket %s has no workspace; %s not sent', ticket.pk, event_type)
        return
    organization_id = queue.organisation.organization_id
    endpoint_ids = list(
        WebhookEndpoint.objects.filter(
            organization_id=organization_id,
            project_id=queue.project_id,
            is_active=True,
            events__contains=[event_type],
        ).values_list('id', flat=True)
    )
    if not endpoint_ids:
        return
    data = {'ticket': PublicTicketSerializer(ticket).data, **extra}
    _enqueue(endpoint_ids, build_event(event_type, organization_id=organization_id, project_id=queue.project_id, data=data))


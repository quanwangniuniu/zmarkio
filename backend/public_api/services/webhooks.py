"""
Outbound webhooks: endpoint secrets, signing, and turning ticket events into deliveries.

Signature: `X-Zmarkio-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>.<body>")>`,
where body is the exact request body. Receivers recompute it with their secret,
compare in constant time, and reject stale timestamps to stop replays.
"""

import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from functools import partial

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from core.crypto import decrypt_token, encrypt_token
from public_api.models import WebhookEndpoint
from public_api.scopes import PING, SLA_BREACHED, TICKET_STATUS_CHANGED

logger = logging.getLogger(__name__)

SECRET_PREFIX = 'whsec_'
SIGNATURE_HEADER = 'X-Zmarkio-Signature'
USER_AGENT = 'Zmarkio-Webhooks/1'


# ---------------------------------------------------------------------------
# Secrets and signing
# ---------------------------------------------------------------------------

def generate_secret():
    return f'{SECRET_PREFIX}{secrets.token_urlsafe(32)}'


def endpoint_secret(endpoint):
    return decrypt_token(endpoint.secret_encrypted)


def canonical_json(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), cls=DjangoJSONEncoder)


def sign(secret, timestamp, body):
    message = f'{timestamp}.'.encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def delivery_headers(endpoint, payload, body, attempt):
    timestamp = int(time.time())
    return {
        'Content-Type': 'application/json',
        'User-Agent': USER_AGENT,
        SIGNATURE_HEADER: f't={timestamp},v1={sign(endpoint_secret(endpoint), timestamp, body)}',
        'X-Zmarkio-Event': payload['type'],
        'X-Zmarkio-Delivery': payload['id'],
        'X-Zmarkio-Attempt': str(attempt),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def create_endpoint(*, organization_id, project_id, url, events, user, description='', is_active=True):
    """Return (endpoint, signing secret). The secret is not recoverable from the API afterwards."""
    secret = generate_secret()
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


def rotate_secret(endpoint):
    secret = generate_secret()
    endpoint.secret_encrypted = encrypt_token(secret)
    endpoint.save(update_fields=['secret_encrypted', 'updated_at'])
    return secret


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
    # Round-trip so dates and decimals are plain JSON for Celery and the delivery log.
    return json.loads(canonical_json(payload))


def _enqueue(endpoint_ids, payload):
    from public_api.tasks import deliver_webhook

    for endpoint_id in endpoint_ids:
        # After commit: nothing is announced for a write that rolls back.
        transaction.on_commit(partial(deliver_webhook.delay, endpoint_id, payload))


def _ticket_workspace(ticket):
    queue = ticket.queue
    if queue is None or queue.organisation is None:
        return None, None
    return queue.organisation.organization_id, queue.project_id


def _ticket_data(ticket):
    from public_api.serializers import PublicTicketSerializer

    return {'ticket': PublicTicketSerializer(ticket).data}


def emit_ticket_event(event_type, ticket, **extra):
    """Queue `event_type` for every active endpoint of the ticket's workspace subscribed to it."""
    organization_id, project_id = _ticket_workspace(ticket)
    if organization_id is None or project_id is None:
        logger.debug('Ticket %s has no workspace; %s not sent', ticket.pk, event_type)
        return
    endpoint_ids = list(
        WebhookEndpoint.objects.filter(
            organization_id=organization_id,
            project_id=project_id,
            is_active=True,
            events__contains=[event_type],
        ).values_list('id', flat=True)
    )
    if not endpoint_ids:
        return
    data = {**_ticket_data(ticket), **extra}
    _enqueue(endpoint_ids, build_event(event_type, organization_id=organization_id, project_id=project_id, data=data))


def emit_status_changed(ticket, previous_status):
    emit_ticket_event(TICKET_STATUS_CHANGED, ticket, previous_status=previous_status)


def emit_sla_breach(ticket, breach_type):
    emit_ticket_event(SLA_BREACHED, ticket, breach_type=breach_type)


def send_test(endpoint):
    payload = build_event(
        PING,
        organization_id=endpoint.organization_id,
        project_id=endpoint.project_id,
        data={'message': 'Test event from the Zmarkio admin console.'},
    )
    _enqueue([endpoint.id], payload)
    return payload


def redeliver(delivery):
    """Start a fresh attempt chain for a logged event; it keeps its event id."""
    _enqueue([delivery.endpoint_id], delivery.payload)

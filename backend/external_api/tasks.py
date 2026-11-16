"""Signed webhook delivery.

A failed delivery is retried three times, at 30s, 2min and 8min. A URL the
server must not call is logged once and not retried.
"""

import hashlib
import hmac
import json
from functools import partial
from urllib.parse import urlparse

import requests
from celery import shared_task
from django.db import transaction

from chat.services import UnsafeUrlError, _pinned_address, resolve_public_url
from core.crypto import decrypt_token
from external_api.models import WebhookDelivery, WebhookEndpoint


def validate_webhook_url(url):
    """Return (normalized url, vetted address). Only public https URLs pass."""
    if urlparse(url or '').scheme.lower() != 'https':
        raise UnsafeUrlError('Webhook URLs must use https.')
    return resolve_public_url(url)


def emit_ticket_event(event_type, ticket, **extra):
    """Queue `event_type` for every subscribed endpoint once the write commits."""
    queue = ticket.queue
    organisation = queue.organisation
    if organisation is None or not organisation.organization_id or not queue.project_id:
        return
    endpoint_ids = WebhookEndpoint.objects.filter(
        organization_id=organisation.organization_id,
        project_id=queue.project_id,
        is_deleted=False,
        events__contains=[event_type],
    ).values_list('id', flat=True)
    payload = {
        'type': event_type,
        'data': {
            'ticket_id': ticket.id,
            'title': ticket.title,
            'status': ticket.status,
            'queue_id': queue.id,
            **extra,
        },
    }
    for endpoint_id in endpoint_ids:
        transaction.on_commit(partial(deliver_webhook.delay, endpoint_id, payload))


@shared_task(bind=True, max_retries=3)
def deliver_webhook(self, endpoint_id, payload):
    endpoint = WebhookEndpoint.objects.filter(pk=endpoint_id, is_deleted=False).first()
    if endpoint is None:
        return None
    body = json.dumps(payload).encode()
    signature = hmac.new(decrypt_token(endpoint.secret_encrypted).encode(), body, hashlib.sha256).hexdigest()
    response_code, retryable = None, True
    try:
        url, address = validate_webhook_url(endpoint.url)
        with _pinned_address(urlparse(url).hostname, address):
            response_code = requests.post(
                url,
                data=body,
                headers={'Content-Type': 'application/json', 'X-Zmarkio-Signature': f'sha256={signature}'},
                timeout=10,
                allow_redirects=False,
            ).status_code
    except UnsafeUrlError:
        retryable = False
    except requests.RequestException:
        pass

    WebhookDelivery.objects.create(
        endpoint=endpoint,
        event_type=payload['type'],
        attempt=self.request.retries + 1,
        response_code=response_code,
    )
    succeeded = response_code is not None and 200 <= response_code < 300
    if not succeeded and retryable and self.request.retries < self.max_retries:
        raise self.retry(countdown=30 * 4 ** self.request.retries)
    return response_code

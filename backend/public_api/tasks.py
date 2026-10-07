import time
from datetime import timedelta

import requests
from celery import shared_task
from django.utils import timezone

from chat.services import UnsafeUrlError
from public_api.models import WebhookDelivery, WebhookEndpoint
from public_api.services.http import post_json_safely
from public_api.services.webhooks import canonical_json, delivery_headers

# Seconds to wait before attempts 2, 3 and 4: three retries, each 4x longer.
BACKOFF_SECONDS = {1: 30, 2: 120, 3: 480}
MAX_ATTEMPTS = 1 + len(BACKOFF_SECONDS)
MAX_ERROR_CHARS = 1000


@shared_task
def deliver_webhook(endpoint_id, payload, attempt=1):
    """
    POST one event to one endpoint, record the attempt, and schedule the next
    one on failure. Every table touched here is in the public schema, so no
    tenant schema context is needed.
    """
    endpoint = WebhookEndpoint.objects.filter(pk=endpoint_id, is_active=True).first()
    if endpoint is None:
        return None  # deleted or deactivated since the event: stop

    delivery = WebhookDelivery.objects.create(
        endpoint=endpoint,
        event_id=payload['id'],
        event_type=payload['type'],
        target_url=endpoint.url,
        payload=payload,
        attempt=attempt,
    )
    body = canonical_json(payload).encode()
    retryable = True
    started = time.monotonic()
    try:
        response = post_json_safely(endpoint.url, body, delivery_headers(endpoint, payload, body, attempt))
        delivery.response_code = response.status_code
        response.close()  # the body is never read or stored
        succeeded = 200 <= response.status_code < 300
        delivery.error = '' if succeeded else f'HTTP {response.status_code}'
    except UnsafeUrlError as exc:
        succeeded, retryable = False, False
        delivery.error = f'Blocked: {exc}'
    except requests.RequestException as exc:
        succeeded = False
        delivery.error = f'{type(exc).__name__}: {exc}'
    delivery.duration_ms = int((time.monotonic() - started) * 1000)
    delivery.error = delivery.error[:MAX_ERROR_CHARS]

    if succeeded:
        delivery.status = WebhookDelivery.Status.SUCCEEDED
    elif retryable and attempt < MAX_ATTEMPTS:
        countdown = BACKOFF_SECONDS[attempt]
        delivery.status = WebhookDelivery.Status.RETRYING
        delivery.next_retry_at = timezone.now() + timedelta(seconds=countdown)
        deliver_webhook.apply_async(args=(endpoint_id, payload, attempt + 1), countdown=countdown)
    else:
        delivery.status = WebhookDelivery.Status.FAILED
    delivery.save(update_fields=['status', 'response_code', 'error', 'duration_ms', 'next_retry_at'])
    return delivery.status

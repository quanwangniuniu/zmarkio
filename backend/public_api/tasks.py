import requests
from celery import shared_task

from chat.services import UnsafeUrlError
from public_api.models import WebhookDelivery, WebhookEndpoint
from public_api.services import canonical_json, delivery_headers, post_json_safely

# Seconds to wait before attempts 2, 3 and 4: three retries, each 4x longer.
BACKOFF_SECONDS = {1: 30, 2: 120, 3: 480}
MAX_ATTEMPTS = 1 + len(BACKOFF_SECONDS)


@shared_task
def deliver_webhook(endpoint_id, payload, attempt=1):
    """
    POST one event to one endpoint, log the attempt, and schedule the next one
    on failure. Every table touched here is in the public schema, so no tenant
    schema context is needed.
    """
    endpoint = WebhookEndpoint.objects.filter(pk=endpoint_id).first()
    if endpoint is None:
        return None  # deleted since the event: stop

    body = canonical_json(payload).encode()
    response_code, error, retryable = None, '', True
    try:
        response = post_json_safely(endpoint.url, body, delivery_headers(endpoint, payload, body, attempt))
        response_code = response.status_code
        response.close()  # the body is never read or stored
        if not 200 <= response_code < 300:
            error = f'HTTP {response_code}'
    except UnsafeUrlError as exc:
        error, retryable = f'Blocked: {exc}', False
    except requests.RequestException as exc:
        error = f'{type(exc).__name__}: {exc}'

    if not error:
        status = WebhookDelivery.Status.SUCCEEDED
    elif retryable and attempt < MAX_ATTEMPTS:
        status = WebhookDelivery.Status.RETRYING
        deliver_webhook.apply_async(args=(endpoint_id, payload, attempt + 1), countdown=BACKOFF_SECONDS[attempt])
    else:
        status = WebhookDelivery.Status.FAILED
    WebhookDelivery.objects.create(
        endpoint=endpoint,
        event_id=payload['id'],
        event_type=payload['type'],
        target_url=endpoint.url,
        attempt=attempt,
        status=status,
        response_code=response_code,
        error=error[:1000],
    )
    return status

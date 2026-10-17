import requests
from celery import shared_task

from chat.services import UnsafeUrlError
from external_api.models import WebhookDelivery, WebhookEndpoint
from external_api.services import canonical_json, delivery_headers, post_json_safely

# Three retries, each 4x later than the one before: 30 s, 2 min, 8 min.
RETRY_BASE_SECONDS = 30


@shared_task(bind=True, max_retries=3)
def deliver_webhook(self, endpoint_id, payload):
    """
    POST one event to one endpoint and log the attempt; a failed attempt is
    retried by Celery. Every table touched here is in the public schema, so no
    tenant schema context is needed.
    """
    endpoint = WebhookEndpoint.objects.filter(pk=endpoint_id).first()
    if endpoint is None:
        return None  # deleted since the event: stop

    attempt = self.request.retries + 1
    body = canonical_json(payload).encode()
    response_code, error, retryable = None, '', True
    try:
        response = post_json_safely(endpoint.url, body, delivery_headers(endpoint, payload, body))
        response_code = response.status_code
        response.close()  # the body is never read or stored
        if not 200 <= response_code < 300:
            error = f'HTTP {response_code}'
    except UnsafeUrlError as exc:
        error, retryable = f'Blocked: {exc}', False
    except requests.RequestException as exc:
        error = f'{type(exc).__name__}: {exc}'

    retry = bool(error) and retryable and self.request.retries < self.max_retries
    if not error:
        status = WebhookDelivery.Status.SUCCEEDED
    else:
        status = WebhookDelivery.Status.RETRYING if retry else WebhookDelivery.Status.FAILED
    WebhookDelivery.objects.create(
        endpoint=endpoint,
        event_type=payload['type'],
        attempt=attempt,
        status=status,
        response_code=response_code,
        error=error[:1000],
    )
    if retry:
        raise self.retry(countdown=RETRY_BASE_SECONDS * 4 ** self.request.retries)
    return status

"""Delivery, signing, retries and the SSRF guard for webhooks."""
import hashlib
import hmac
from unittest.mock import MagicMock, patch

import pytest
import requests

from chat.services import UnsafeUrlError
from core.crypto import decrypt_token
from public_api.models import WebhookDelivery
from public_api.services.webhooks import build_event, create_endpoint, post_json_safely, validate_webhook_url
from public_api.tasks import BACKOFF_SECONDS, deliver_webhook
from public_api.tests.conftest import resolving_to

pytestmark = pytest.mark.django_db


@pytest.fixture
def endpoint(organization, project):
    endpoint, secret = create_endpoint(
        organization_id=organization.id, project_id=project.id,
        url='https://hooks.example.com/zmarkio', events=['ticket.created'], user=None,
    )
    return endpoint


@pytest.fixture
def payload(organization, project):
    return build_event('ticket.created', organization_id=organization.id, project_id=project.id, data={'ticket': {'id': 1}})


def _response(code):
    response = MagicMock()
    response.status_code = code
    return response


class TestDelivery:
    def test_success_is_signed_and_logged(self, endpoint, payload):
        with patch('public_api.tasks.post_json_safely', return_value=_response(204)) as post:
            assert deliver_webhook(endpoint.id, payload) == WebhookDelivery.Status.SUCCEEDED

        url, body, headers = post.call_args.args
        assert url == endpoint.url
        timestamp, signature = (part.split('=', 1)[1] for part in headers['X-Zmarkio-Signature'].split(','))
        expected = hmac.new(decrypt_token(endpoint.secret_encrypted).encode(), f'{timestamp}.'.encode() + body, hashlib.sha256).hexdigest()
        assert hmac.compare_digest(signature, expected)
        assert headers['X-Zmarkio-Event'] == 'ticket.created'
        assert headers['X-Zmarkio-Delivery'] == payload['id']

        row = WebhookDelivery.objects.get()
        assert (row.target_url, row.event_type, row.response_code, row.attempt) == (endpoint.url, 'ticket.created', 204, 1)
        assert row.created_at is not None

    def test_secret_is_stored_encrypted(self, endpoint):
        assert decrypt_token(endpoint.secret_encrypted).startswith('whsec_')
        assert decrypt_token(endpoint.secret_encrypted) not in endpoint.secret_encrypted

    def test_failures_retry_three_times_with_backoff_then_fail(self, endpoint, payload):
        countdowns = []
        with patch('public_api.tasks.post_json_safely', return_value=_response(500)), \
                patch('public_api.tasks.deliver_webhook.apply_async',
                      side_effect=lambda args, countdown: countdowns.append(countdown)):
            for attempt in (1, 2, 3, 4):
                deliver_webhook(endpoint.id, payload, attempt)

        rows = list(WebhookDelivery.objects.order_by('attempt'))
        assert [r.attempt for r in rows] == [1, 2, 3, 4]
        assert [r.status for r in rows] == ['retrying', 'retrying', 'retrying', 'failed']
        assert all(r.response_code == 500 and r.event_id == rows[0].event_id for r in rows)
        assert countdowns == [BACKOFF_SECONDS[1], BACKOFF_SECONDS[2], BACKOFF_SECONDS[3]]
        assert countdowns[0] < countdowns[1] < countdowns[2]

    def test_network_error_is_retried(self, endpoint, payload):
        with patch('public_api.tasks.post_json_safely', side_effect=requests.ConnectTimeout('slow')), \
                patch('public_api.tasks.deliver_webhook.apply_async') as retry:
            deliver_webhook(endpoint.id, payload)

        row = WebhookDelivery.objects.get()
        assert row.status == 'retrying' and row.response_code is None and 'ConnectTimeout' in row.error
        retry.assert_called_once()

    def test_blocked_target_is_not_retried(self, endpoint, payload):
        with patch('public_api.tasks.post_json_safely', side_effect=UnsafeUrlError('private')), \
                patch('public_api.tasks.deliver_webhook.apply_async') as retry:
            deliver_webhook(endpoint.id, payload)

        assert WebhookDelivery.objects.get().status == 'failed'
        retry.assert_not_called()

    def test_deactivated_endpoint_stops_the_chain(self, endpoint, payload):
        endpoint.is_active = False
        endpoint.save()
        with patch('public_api.tasks.post_json_safely') as post:
            assert deliver_webhook(endpoint.id, payload, 2) is None
        post.assert_not_called()
        assert not WebhookDelivery.objects.exists()


class TestUrlGuard:
    def test_public_https_is_allowed(self):
        with resolving_to('93.184.216.34'):
            validate_webhook_url('https://hooks.example.com/x')

    @pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.5', '169.254.169.254', '192.168.1.1'])
    def test_private_targets_are_blocked(self, address):
        with resolving_to(address), pytest.raises(UnsafeUrlError):
            validate_webhook_url('https://internal.example.com/x')

    def test_plain_http_is_blocked(self):
        with resolving_to('93.184.216.34'), pytest.raises(UnsafeUrlError):
            validate_webhook_url('http://hooks.example.com/x')

    def test_redirects_are_not_followed(self):
        with resolving_to('93.184.216.34'), patch('public_api.services.webhooks.requests.post') as post:
            post_json_safely('https://hooks.example.com/x', b'{}', {})
        assert post.call_args.kwargs['allow_redirects'] is False

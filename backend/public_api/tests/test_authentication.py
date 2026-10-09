"""API key and OAuth 2.0 authentication for /api/v1/."""
import pytest
from django.urls import reverse

from public_api.services.credentials import revoke_api_key, revoke_oauth_client
from public_api.tests.conftest import client_for

pytestmark = pytest.mark.django_db

TICKETS = reverse('public-api-ticket-list')


def _token(api_client, client_id, secret):
    return api_client.post(reverse('public-api-oauth-token'), {
        'grant_type': 'client_credentials', 'client_id': client_id, 'client_secret': secret,
    })


class TestApiKey:
    def test_valid_key_authenticates(self, key_client):
        assert key_client.get(TICKETS).status_code == 200

    def test_missing_credential_is_401_with_challenge(self, api_client):
        response = api_client.get(TICKETS)

        assert response.status_code == 401
        assert response['WWW-Authenticate'] == 'Api-Key realm="api"'

    @pytest.mark.parametrize('raw', ['nonsense', 'zmk_deadbeef_wrong', 'zmk__', 'Bearer x'])
    def test_malformed_or_unknown_key_is_401(self, raw):
        assert client_for(raw).get(TICKETS).status_code == 401

    def test_wrong_secret_for_real_prefix_is_401(self, api_key):
        key, _ = api_key
        assert client_for(f'zmk_{key.prefix}_not-the-secret').get(TICKETS).status_code == 401

    def test_revoked_key_is_401(self, api_key):
        revoke_api_key(api_key[0])
        assert client_for(api_key[1]).get(TICKETS).status_code == 401

    def test_inactive_workspace_is_401(self, api_key, organization):
        organization.is_active = False
        organization.save(update_fields=['is_active'])
        assert client_for(api_key[1]).get(TICKETS).status_code == 401

    def test_only_a_hash_of_the_secret_is_stored(self, api_key):
        key, raw = api_key
        assert raw.split('_', 2)[2] not in key.key_hash


class TestOAuthClient:
    def test_client_credentials_token_authenticates(self, api_client, oauth_client):
        client, secret = oauth_client
        token_response = _token(api_client, client.client_id, secret)

        assert token_response.status_code == 200, token_response.content
        body = token_response.json()
        assert body['token_type'] == 'Bearer'
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access_token']}")
        assert api_client.get(TICKETS).status_code == 200

    def test_wrong_secret_gets_no_token(self, api_client, oauth_client):
        assert _token(api_client, oauth_client[0].client_id, 'wrong').status_code == 401

    def test_unknown_bearer_is_401(self, api_client):
        api_client.credentials(HTTP_AUTHORIZATION='Bearer not-a-token')
        assert api_client.get(TICKETS).status_code == 401

    def test_revoke_invalidates_issued_tokens_and_blocks_new_ones(self, api_client, oauth_client):
        client, secret = oauth_client
        access_token = _token(api_client, client.client_id, secret).json()['access_token']

        revoke_oauth_client(client)

        api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        assert api_client.get(TICKETS).status_code == 401
        api_client.credentials()
        assert _token(api_client, client.client_id, secret).status_code == 401

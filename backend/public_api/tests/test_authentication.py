"""API key and OAuth 2.0 authentication for /api/v1/ (MED-226)."""
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from public_api.models import ApiKey
from public_api.services.credentials import revoke_api_key, revoke_oauth_client

pytestmark = pytest.mark.django_db

WHOAMI = '/api/v1/csm/whoami/'
TOKEN = '/api/v1/oauth/token/'


def _get(raw_key=None, bearer=None):
    client = APIClient()
    if raw_key is not None:
        client.credentials(HTTP_X_API_KEY=raw_key)
    if bearer is not None:
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {bearer}')
    return client.get(WHOAMI)


def _token(client_id, secret):
    return APIClient().post(TOKEN, {
        'grant_type': 'client_credentials', 'client_id': client_id, 'client_secret': secret,
    })


class TestApiKey:
    def test_valid_key_identifies_workspace_project_and_scopes(self, api_key, organization, project):
        response = _get(api_key[1])

        assert response.status_code == 200
        assert response.data['credential_type'] == 'api_key'
        assert response.data['organization'] == {'id': organization.id, 'slug': organization.slug}
        assert response.data['project_id'] == project.id
        assert 'tickets:write' in response.data['scopes']

    def test_missing_credential_is_401_with_challenge(self):
        response = _get()

        assert response.status_code == 401
        assert response['WWW-Authenticate'] == 'Api-Key realm="api"'

    @pytest.mark.parametrize('raw', ['nonsense', 'zmk_deadbeef_wrong', 'zmk__', 'Bearer x'])
    def test_malformed_or_unknown_key_is_401(self, raw):
        assert _get(raw).status_code == 401

    def test_wrong_secret_for_real_prefix_is_401(self, api_key):
        key, _ = api_key
        assert _get(f'zmk_{key.prefix}_not-the-secret').status_code == 401

    def test_revoked_key_is_401(self, api_key):
        revoke_api_key(api_key[0])
        assert _get(api_key[1]).status_code == 401

    def test_expired_key_is_401(self, make_key):
        key, raw = make_key()
        ApiKey.objects.filter(pk=key.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        assert _get(raw).status_code == 401

    def test_inactive_workspace_is_401(self, api_key, organization):
        organization.is_active = False
        organization.save(update_fields=['is_active'])
        assert _get(api_key[1]).status_code == 401

    def test_use_records_last_used(self, api_key):
        _get(api_key[1])
        api_key[0].refresh_from_db()
        assert api_key[0].last_used_at is not None

    def test_stored_hash_is_not_the_secret(self, api_key):
        key, raw = api_key
        assert raw.split('_', 2)[2] not in key.key_hash


class TestOAuthClient:
    def test_client_credentials_token_authenticates(self, oauth_client, project):
        client, secret = oauth_client
        token_response = _token(client.client_id, secret)

        assert token_response.status_code == 200, token_response.content
        body = token_response.json()
        assert body['token_type'] == 'Bearer'

        response = _get(bearer=body['access_token'])
        assert response.status_code == 200
        assert response.data['credential_type'] == 'oauth_client'
        assert response.data['project_id'] == project.id

    def test_wrong_secret_gets_no_token(self, oauth_client):
        assert _token(oauth_client[0].client_id, 'wrong').status_code == 401

    def test_unknown_bearer_is_401(self):
        assert _get(bearer='not-a-token').status_code == 401

    def test_revoke_invalidates_issued_tokens_and_blocks_new_ones(self, oauth_client):
        client, secret = oauth_client
        access_token = _token(client.client_id, secret).json()['access_token']

        revoke_oauth_client(client)

        assert _get(bearer=access_token).status_code == 401
        assert _token(client.client_id, secret).status_code == 401

    def test_token_never_carries_a_human_user(self, oauth_client, user):
        # Even if the toolkit application were linked to a person, the request acts as the client.
        client, secret = oauth_client
        client.application.user = user
        client.application.save(update_fields=['user'])
        access_token = _token(client.client_id, secret).json()['access_token']

        response = _get(bearer=access_token)

        assert response.status_code == 200
        assert response.data['name'] == 'CRM sync'

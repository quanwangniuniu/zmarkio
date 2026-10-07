"""Create, verify and revoke public API credentials."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from oauth2_provider.generators import generate_client_secret
from oauth2_provider.models import get_application_model

from public_api.models import ApiKey, OAuthClient

KEY_PREFIX = 'zmk'
# last_used_at is informational; writing it on every request would add a write per call.
LAST_USED_RESOLUTION = timedelta(minutes=1)


def _hash_secret(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def _new_prefix():
    while True:
        prefix = secrets.token_hex(4)
        if not ApiKey.objects.filter(prefix=prefix).exists():
            return prefix


def create_api_key(*, organization_id, project_id, name, scopes, user):
    """Return (ApiKey, full key). The full key is not recoverable afterwards."""
    prefix = _new_prefix()
    secret = secrets.token_urlsafe(32)
    key = ApiKey.objects.create(
        organization_id=organization_id,
        project_id=project_id,
        name=name,
        prefix=prefix,
        key_hash=_hash_secret(secret),
        scopes=scopes,
        created_by=user,
    )
    return key, f'{KEY_PREFIX}_{prefix}_{secret}'


def verify_api_key(raw):
    """The active ApiKey for `raw`, or None. The organisation is loaded with it."""
    parts = raw.split('_', 2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        return None
    _, prefix, secret = parts
    key = ApiKey.objects.select_related('organization').filter(prefix=prefix).first()
    if key is None or not hmac.compare_digest(key.key_hash, _hash_secret(secret)):
        return None
    if not key.is_active or not key.organization.is_active:
        return None
    return key


def touch_last_used(key):
    now = timezone.now()
    if key.last_used_at is None or now - key.last_used_at >= LAST_USED_RESOLUTION:
        ApiKey.objects.filter(pk=key.pk).update(last_used_at=now)
        key.last_used_at = now


def revoke_api_key(key):
    if key.revoked_at is None:
        key.revoked_at = timezone.now()
        key.save(update_fields=['revoked_at', 'updated_at'])
    return key


@transaction.atomic
def create_oauth_client(*, organization_id, project_id, name, scopes, user):
    """Return (OAuthClient, client secret). The secret is stored hashed by the toolkit."""
    Application = get_application_model()
    secret = generate_client_secret()
    application = Application.objects.create(
        name=name,
        client_type=Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS,
        client_secret=secret,
        skip_authorization=True,
    )
    client = OAuthClient.objects.create(
        organization_id=organization_id,
        project_id=project_id,
        name=name,
        application=application,
        client_id=application.client_id,
        scopes=scopes,
        created_by=user,
    )
    return client, secret


@transaction.atomic
def revoke_oauth_client(client):
    """Delete the toolkit application, which invalidates every token it was issued."""
    application = client.application
    client.application = None
    client.revoked_at = client.revoked_at or timezone.now()
    client.save(update_fields=['application', 'revoked_at', 'updated_at'])
    if application is not None:
        application.delete()
    return client

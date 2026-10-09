"""
Authentication for the public API: `X-API-Key` keys and OAuth 2.0 bearer tokens.

Both resolve to an ApiPrincipal bound to one (organization, project) and switch
the connection to that organisation's tenant schema for the rest of the
request. TenantSchemaMiddleware only understands JWTs, so it leaves these
requests on `public`; its `finally` still resets the search_path afterwards.
Credential rows live in `public` only, so the lookups resolve there whatever the
search_path is when DRF authenticates.
"""

from django.db import connection
from oauth2_provider.contrib.rest_framework import OAuth2Authentication
from psycopg2 import sql
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from core.services.tenant import slug_to_schema_name
from public_api.models import OAuthClient
from public_api.services import verify_api_key


class ApiPrincipal:
    """
    `request.user` for a request authenticated by an API key or OAuth client.

    It is deliberately not a user: no human's role, membership or staff flag
    applies, so internal permission checks that look at a user cannot grant it
    anything. Everything it may see is derived from (organization_id, project_id).
    """

    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_staff = False
    is_superuser = False
    pk = None
    id = None

    def __init__(self, *, kind, credential_id, name, organization_id, project_id, scopes):
        self.kind = kind
        self.credential_id = credential_id
        self.name = name
        self.organization_id = organization_id
        self.project_id = project_id
        self.scopes = list(scopes)

    def __str__(self):
        return f'{self.kind} {self.name}'


def activate_tenant(organization):
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL('SET search_path TO {}, public').format(
                sql.Identifier(slug_to_schema_name(organization.slug)),
            )
        )


def _principal(kind, credential):
    return ApiPrincipal(
        kind=kind,
        credential_id=credential.pk,
        name=credential.name,
        organization_id=credential.organization_id,
        project_id=credential.project_id,
        scopes=credential.scopes,
    )


class ApiKeyAuthentication(BaseAuthentication):
    header = 'HTTP_X_API_KEY'

    def authenticate(self, request):
        raw = request.META.get(self.header, '').strip()
        if not raw:
            return None
        key = verify_api_key(raw)
        if key is None:
            raise AuthenticationFailed('Invalid, expired or revoked API key.')
        activate_tenant(key.organization)
        return _principal('api_key', key), key

    def authenticate_header(self, request):
        # Makes DRF answer a missing or bad credential with 401, not 403.
        return 'Api-Key realm="api"'


class OAuthClientAuthentication(OAuth2Authentication):
    """Accepts toolkit bearer tokens only for applications bound to an active OAuthClient."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        token = result[1]
        client = (
            OAuthClient.objects.select_related('organization')
            .filter(application_id=token.application_id, revoked_at__isnull=True)
            .first()
        )
        if client is None or not client.organization.is_active:
            raise AuthenticationFailed('Invalid or revoked OAuth client.')
        activate_tenant(client.organization)
        return _principal('oauth_client', client), token

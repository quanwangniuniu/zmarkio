"""
API-key and OAuth bearer authentication for the external API.

Both switch to the credential's tenant schema; TenantSchemaMiddleware only reads
JWTs but its `finally` still resets the search_path after the request.
"""

from django.db import connection
from oauth2_provider.contrib.rest_framework import OAuth2Authentication
from psycopg2 import sql
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from core.services.tenant import slug_to_schema_name
from external_api.models import OAuthClient
from external_api.services import verify_api_key


class ApiPrincipal:
    """`request.user` for a credential: deliberately not a User, so no human's role applies."""

    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_staff = False
    is_superuser = False
    pk = None
    id = None

    def __init__(self, credential):
        self.organization_id = credential.organization_id
        self.project_id = credential.project_id
        self.scopes = list(credential.scopes)


def activate_tenant(organization):
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL('SET search_path TO {}, public').format(
                sql.Identifier(slug_to_schema_name(organization.slug)),
            )
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
        return ApiPrincipal(key), key

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
            .filter(application_id=token.application_id)
            .first()
        )
        if client is None or not client.organization.is_active:
            raise AuthenticationFailed('Invalid or revoked OAuth client.')
        activate_tenant(client.organization)
        return ApiPrincipal(client), token

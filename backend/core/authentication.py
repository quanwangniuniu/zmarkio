"""
Tenant-aware JWT authentication.

Ensures user lookups always happen in the public schema, regardless of which
tenant schema the request is currently operating in.
"""
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from django.db import connection
from authentication.session_registry import SessionRegistry


class TenantAwareJWTAuthentication(JWTAuthentication):
    """
    JWT authentication that handles multi-tenant schema switching.

    CRITICAL: In a multi-tenant environment, user data lives in the public
    schema, but TenantSchemaMiddleware may have already switched search_path
    to a tenant schema by the time DRF's authentication runs at the view layer.

    This class temporarily switches back to public schema to query users,
    then restores the original search_path.

    NOTE: This class is largely redundant now that TenantSchemaMiddleware
    does early JWT authentication. However, it serves as a safety net if
    middleware authentication fails for any reason.
    """

    def get_user(self, validated_token):
        """
        Override to query user from public schema, not tenant schema.
        """
        # Save current search_path
        with connection.cursor() as cursor:
            cursor.execute('SHOW search_path')
            original_path = cursor.fetchone()[0]

        # Temporarily switch to public for user lookup
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO public')

        try:
            # Call parent's get_user() which queries the user model
            user = super().get_user(validated_token)
            token_version = int(validated_token.get("auth_token_version", 0))
            if token_version != getattr(user, "auth_token_version", 0):
                raise AuthenticationFailed("Token has been revoked.", code="token_revoked")
            # Use refresh_jti (embedded from the refresh token) to match the
            # session registry, which keys sessions by refresh token JTI.
            refresh_jti = validated_token.get("refresh_jti")
            if refresh_jti and SessionRegistry.is_evicted(refresh_jti):
                raise AuthenticationFailed("Session has been evicted.", code="session_evicted")
            return user
        finally:
            # Restore original search_path
            # CRITICAL: Cannot use parameter substitution (cursor.execute('SET search_path TO %s', [path]))
            # because psycopg2 will quote the value, turning "schema1, public" into '"schema1, public"'
            # (a single quoted identifier) instead of two separate schemas.
            # We must use raw SQL here since original_path comes from PostgreSQL itself (SHOW search_path)
            # and is already validated.
            with connection.cursor() as cursor:
                cursor.execute(f'SET search_path TO {original_path}')

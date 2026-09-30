"""
Per-request PostgreSQL search_path switcher.

How it works
------------
1. After Django's AuthenticationMiddleware populates request.user, this
   middleware resolves which org schema the request belongs to.
2. It issues SET search_path TO <schema>, public for the current DB
   connection so that all subsequent ORM queries in the same request run
   against the correct tenant schema automatically — without any change to
   view or model code.
3. A finally block resets the search_path to 'public' before returning the
   connection to Django's connection pool, preventing cross-request pollution.

Placement in MIDDLEWARE (settings.py)
--------------------------------------
Must appear AFTER 'django.contrib.auth.middleware.AuthenticationMiddleware'
(so request.user is available) and BEFORE any custom middleware that issues
DB queries scoped to a project or org.

Recommended position (see settings.py line ~121):
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'core.middleware.tenant_schema.TenantSchemaMiddleware',   # ← add here
    'core.middleware.project_access.CheckProjectAccessMiddleware',

Schema resolution priority
--------------------------
1. Authenticated user  → user.organization.slug  (JWT-backed, not spoofable)
2. X-Organization-Token header → decrypted org slug (org-scoped encrypted JWT;
   also used by tests that rely on DRF force_authenticate instead of JWT)
3. X-Organization-Slug header → validated against DB + cache (service calls)
4. Fallback → 'public'  (login, register, health-check, etc.)

Caching
-------
Org slug lookups are cached (Django default cache, TTL = 5 min) to avoid an
extra DB round-trip on every request.  Org slugs change extremely rarely;
the TTL is a safe trade-off.
"""

import logging

from django.core.cache import cache
from django.db import connection
from psycopg2 import sql

from core.services.tenant import slug_to_schema_name

logger = logging.getLogger(__name__)

_CACHE_TTL = 300  # seconds (5 minutes)


class TenantSchemaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # CRITICAL: DRF JWT authentication happens at the view layer, not in
        # AuthenticationMiddleware. We must manually authenticate JWT tokens here
        # so _resolve_schema() can see the authenticated user.
        self._authenticate_jwt(request)

        schema = self._resolve_schema(request)

        # SET search_path requires unquoted identifiers, not string literals.
        # Use psycopg2.sql.Identifier for injection-safe identifier composition:
        #   SET search_path TO org_acme_corp, public
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL('SET search_path TO {}, public').format(sql.Identifier(schema))
            )

        try:
            return self.get_response(request)
        finally:
            # Defense in depth: reset search_path so nothing that runs later on
            # this connection inherits this tenant's schema. In production
            # CONN_MAX_AGE=0 makes Django close the connection on
            # request_finished, and PgBouncer (session mode) runs DISCARD ALL
            # before reusing the server connection. The reset matters for tests
            # (one connection reused across tests) and for outer middleware that
            # query the DB after this one returns.
            #
            # Guard against InFailedSqlTransaction: if a view raised a DB error
            # that aborted the current transaction, the SET will fail with
            # InFailedSqlTransaction. Catch and roll back instead of letting a
            # cleanup error replace the view's response or exception.
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SET search_path TO public')
            except Exception:
                logger.warning(
                    "TenantSchemaMiddleware: failed to reset search_path to public "
                    "after %s %s; rolling back",
                    request.method, request.path, exc_info=True,
                )
                # Do NOT call connection.close() here: Django's test runner
                # wraps every test in a transaction/savepoint using the same
                # connection object, so closing it causes
                # "InterfaceError: connection already closed" in the next test.
                try:
                    connection.rollback()
                except Exception:
                    # Expected in tests: Django forbids rollback() inside the
                    # TestCase atomic block. In production a failed rollback
                    # means the connection is broken; with CONN_MAX_AGE=0 Django
                    # closes it on request_finished and PgBouncer discards it.
                    logger.debug(
                        "TenantSchemaMiddleware: rollback after failed search_path reset also failed",
                        exc_info=True,
                    )

    # ------------------------------------------------------------------
    # Schema resolution
    # ------------------------------------------------------------------

    def _resolve_schema(self, request) -> str:
        # Priority 1: authenticated user (comes from signed JWT — cannot be forged)
        if request.user.is_authenticated:
            # Try current_organization first (multi-org support)
            org_id = getattr(request.user, 'current_organization_id', None)

            # Fallback to organization for backward compatibility
            if not org_id:
                org_id = getattr(request.user, 'organization_id', None)

            if org_id:
                slug = self._get_slug_by_org_id(org_id)
                if slug:
                    return slug_to_schema_name(slug)

        # Priority 2: X-Organization-Token header (encrypted org-scoped JWT).
        # Used by stripe_meta / org-admin endpoints and DRF force_authenticate
        # tests where the user is not yet visible to middleware.  The token is
        # signed + encrypted so it cannot be forged; we additionally validate
        # the extracted slug against the DB.
        org_token = request.META.get('HTTP_X_ORGANIZATION_TOKEN')
        if org_token:
            slug = self._decode_org_token(org_token)
            if slug and self._validate_slug(slug):
                return slug_to_schema_name(slug)

        # Priority 3: X-Organization-Slug header (inter-service calls).
        # MUST be validated: any client can set arbitrary headers, so we
        # confirm the slug exists in the DB before trusting it.
        header_slug = request.headers.get('X-Organization-Slug')
        if header_slug and self._validate_slug(header_slug):
            return slug_to_schema_name(header_slug)

        # Fallback: public schema (unauthenticated endpoints, health checks…)
        return 'public'

    @staticmethod
    def _decode_org_token(token: str) -> str | None:
        """
        Decode an X-Organization-Token (signed + encrypted JWT) and return the
        organisation slug embedded in it, or None if the token is invalid.

        Mirrors stripe_meta.permissions.decode_organization_access_token but
        lives here to avoid circular imports between core middleware and
        stripe_meta.
        """
        try:
            import base64
            import json

            import jwt
            from cryptography.fernet import Fernet
            from django.conf import settings

            secret_key = settings.ORGANIZATION_ACCESS_TOKEN_SECRET_KEY
            payload = jwt.decode(token, secret_key, algorithms=['HS256'])

            if payload.get('type') != 'access':
                return None

            encrypted_data = payload.get('encrypted_data')
            if not encrypted_data:
                return None

            encryption_key = settings.ORGANIZATION_ACCESS_TOKEN_ENCRYPTION_KEY.encode()
            fernet = Fernet(encryption_key)
            decrypted_data = fernet.decrypt(base64.b64decode(encrypted_data))
            sensitive_data = json.loads(decrypted_data.decode())

            return sensitive_data.get('organization_slug')
        except Exception:
            return None

    # ------------------------------------------------------------------
    # JWT Authentication
    # ------------------------------------------------------------------

    @staticmethod
    def _authenticate_jwt(request):
        """
        Manually authenticate JWT tokens so we can resolve the tenant schema
        based on the authenticated user. DRF's authentication normally runs at
        the view layer, but we need it earlier.
        """
        from core.authentication import TenantAwareJWTAuthentication
        from rest_framework.exceptions import AuthenticationFailed

        # Skip if already authenticated (e.g. session auth)
        if request.user.is_authenticated:
            return

        try:
            jwt_auth = TenantAwareJWTAuthentication()
            auth_result = jwt_auth.authenticate(request)
            if auth_result is not None:
                user, token = auth_result
                request.user = user
                request.auth = token
        except AuthenticationFailed:
            # Invalid/expired token - leave as AnonymousUser
            pass
        except Exception:
            # Any other error - leave as AnonymousUser
            pass

    # ------------------------------------------------------------------
    # Cache-backed helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_slug_by_org_id(org_id: int) -> str | None:
        """Return the slug for *org_id*, hitting the cache first."""
        cache_key = f'tenant:slug:{org_id}'
        slug = cache.get(cache_key)
        if slug is None:
            from core.models import Organization
            try:
                slug = (
                    Organization.objects
                    .values_list('slug', flat=True)
                    .get(pk=org_id)
                )
                cache.set(cache_key, slug, _CACHE_TTL)
            except Organization.DoesNotExist:
                return None
        return slug

    @staticmethod
    def _validate_slug(slug: str) -> bool:
        """
        Return True if *slug* corresponds to an active Organization.
        Result is cached to avoid a DB hit on every request that sends the header.
        """
        cache_key = f'tenant:valid:{slug}'
        result = cache.get(cache_key)
        if result is None:
            from core.models import Organization
            result = Organization.objects.filter(
                slug=slug, is_active=True
            ).exists()
            cache.set(cache_key, result, _CACHE_TTL)
        return result

"""
Mint JWT fixtures with Django's production simplejwt path for Studio Node auth parity.

Used by CI before variations-studio-api Jest runs. Prints a single JSON object to stdout
(CI captures stdout into a file mounted into the Node test container).

Usage:
  python manage.py issue_studio_jwt_fixtures --org-slug=ci-studio-org
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models.signals import post_save
from rest_framework_simplejwt.tokens import AccessToken

from core.authentication import TenantAwareJWTAuthentication
from core.models import CustomUser, Organization
from core.services.auth_tokens import build_user_refresh_token
from core.services.tenant import provision_tenant_schema


PARITY_EMAIL = "studio-jwt-parity@ci.test"


def _django_accepts_access(raw: str) -> bool:
    try:
        auth = TenantAwareJWTAuthentication()
        validated = auth.get_validated_token(raw)
        auth.get_user(validated)
        return True
    except Exception:
        return False


def _django_rejects_access(raw: str) -> bool:
    return not _django_accepts_access(raw)


def _mint_access(user: CustomUser, *, lifetime: timedelta | None = None) -> str:
    refresh = build_user_refresh_token(user)
    access = refresh.access_token
    if lifetime is not None:
        access.set_exp(lifetime=lifetime)
    return str(access)


class Command(BaseCommand):
    help = (
        "Issue Django simplejwt access/refresh fixtures for variations-studio-api "
        "Node auth parity tests."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--org-slug",
            default="ci-studio-org",
            help="Organization slug whose tenant schema should exist (default: ci-studio-org).",
        )

    def handle(self, *args, **options):
        org_slug = options["org_slug"]
        try:
            org = Organization.objects.get(slug=org_slug, is_active=True)
        except Organization.DoesNotExist as exc:
            raise CommandError(
                f"Active organization with slug={org_slug!r} not found. "
                "Provision it before minting Studio JWT fixtures."
            ) from exc

        try:
            from stripe_meta.signals import create_default_subscription

            post_save.disconnect(create_default_subscription, sender=Organization)
            try:
                provision_tenant_schema(org.slug)
            finally:
                post_save.connect(create_default_subscription, sender=Organization)
        except ImportError:
            provision_tenant_schema(org.slug)

        user = CustomUser.objects.filter(email=PARITY_EMAIL).first()
        if user is None:
            user = CustomUser(
                username="studio-jwt-parity",
                email=PARITY_EMAIL,
                is_active=True,
                is_verified=True,
                password_set=False,
                organization=org,
                current_organization=org,
                auth_token_version=0,
            )
            user.set_unusable_password()
            user.save()
        else:
            user.is_active = True
            user.organization = org
            user.current_organization = org
            user.auth_token_version = 0
            user.set_unusable_password()
            user.save()

        # --- tokens at version 0 (pre-rotation) ---
        pre_rotation = _mint_access(user)
        expired = _mint_access(user, lifetime=timedelta(seconds=-60))
        refresh = str(build_user_refresh_token(user))

        if not _django_accepts_access(pre_rotation):
            raise CommandError("Django rejected a freshly minted version=0 access token.")
        if not _django_rejects_access(expired):
            raise CommandError("Django unexpectedly accepted an expired access token.")
        # Refresh strings are not valid AccessTokens for API auth.
        try:
            AccessToken(refresh)
            refresh_rejected = False
        except Exception:
            refresh_rejected = True
        if not refresh_rejected:
            raise CommandError("Django AccessToken unexpectedly accepted a refresh token.")

        # --- rotate version (password-change / logout equivalent) ---
        user.auth_token_version = 1
        user.save(update_fields=["auth_token_version"])

        access = _mint_access(user)
        # Long enough that CI/local mint→jest gaps (minutes) do not flake; still
        # far shorter than SIMPLE_JWT ACCESS_TOKEN_LIFETIME (days).
        near_expiry = _mint_access(user, lifetime=timedelta(minutes=30))

        if not _django_accepts_access(access):
            raise CommandError("Django rejected the post-rotation access token.")
        if not _django_rejects_access(pre_rotation):
            raise CommandError(
                "Django unexpectedly accepted a pre-rotation token after auth_token_version bump."
            )
        if not _django_accepts_access(near_expiry):
            raise CommandError("Django rejected the near-expiry access token.")

        payload = {
            "user_id": user.id,
            "org_slug": org.slug,
            "auth_token_version": user.auth_token_version,
            "access_token": access,
            "near_expiry_access_token": near_expiry,
            "expired_access_token": expired,
            "refresh_token": refresh,
            "pre_rotation_access_token": pre_rotation,
            "django_accepts_access": True,
            "django_rejects_expired": True,
            "django_rejects_refresh_as_access": True,
            "django_rejects_pre_rotation": True,
            "django_accepts_near_expiry": True,
            "notes": {
                "signing": "HS256 + Django SECRET_KEY via build_user_refresh_token",
                "clock_skew": (
                    "SIMPLE_JWT does not set leeway; Node jwtVerify default leeway is 0. "
                    "Tokens that are already expired are rejected (no clock-skew window)."
                ),
                "rotated_signing_key": (
                    "Covered on the Node side with a JWT signed using a non-SECRET_KEY secret; "
                    "Django and Node both require HS256 + settings.SECRET_KEY."
                ),
            },
        }

        self.stdout.write(json.dumps(payload, separators=(",", ":")))

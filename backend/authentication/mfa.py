"""TOTP multi-factor authentication helpers for org admins (MED-326).

Shared by the enrollment endpoints, the login MFA step and the authorization
middleware. Secrets are stored encrypted (MfaDevice.set_secret); recovery codes
are stored only as keyed HMAC-SHA256 hashes, so the plain codes can be shown once.
"""

from __future__ import annotations

import secrets

import pyotp
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from authentication.middleware import PasswordRotationMiddleware
from authentication.models import MfaDevice, RecoveryCode

MFA_ISSUER_NAME = "MediaJira"
# Accept the current 30-second step plus one step either side, to absorb
# small clock drift between the server and the authenticator app.
TOTP_VALID_WINDOW = 1
RECOVERY_CODE_COUNT = 10
MFA_TOKEN_SALT = "authentication.mfa.login"
MFA_TOKEN_MAX_AGE_SECONDS = 5 * 60
MFA_TOKEN_COOKIE = "mfa_token"
MFA_TOKEN_COOKIE_PATH = "/auth/mfa/"
# Requests an org admin can still make before passing MFA: the same account
# endpoints password rotation leaves open, plus the MFA endpoints themselves.
MFA_EXEMPT_PREFIXES = PasswordRotationMiddleware.EXEMPT_PREFIXES + ("/auth/mfa/",)


def requires_mfa(user) -> bool:
    """Anyone with an org-admin record, in any organization, must use MFA.

    Used by login and enrollment. The middleware applies the same rule, with the
    org-schema check run against the request's active schema.
    """
    from access_control.models import UserRole
    from core.services.tenant import slug_to_schema_name
    from core.tenant_context import tenant_schema_context

    if has_admin_record(user):
        return True
    current_org = getattr(user, "current_organization", None) or getattr(user, "organization", None)
    if not current_org:
        return False
    # Any level-2 role in the org schema, expired or not and whichever org it names,
    # because the middleware's org-admin bypass does not filter on either.
    with tenant_schema_context(slug_to_schema_name(current_org.slug)):
        return UserRole.objects.filter(user=user, role__level=2).exists()


def has_admin_record(user) -> bool:
    """An 'admin' membership or a public-schema level-2 role, in ANY organization.

    Some permission code (get_org_admin_org_ids) honours admin roles outside the
    user's current org, and UserRole can be written to public (e.g. by
    admin_views.assign_admin without org context).
    """
    from access_control.models import UserRole
    from core.models import OrganizationMembership
    from core.tenant_context import tenant_schema_context

    if not getattr(user, "pk", None):
        return False
    if OrganizationMembership.objects.filter(user=user, role="admin", is_active=True).exists():
        return True
    with tenant_schema_context("public"):
        return UserRole.objects.filter(user=user, role__level=2).exists()


def get_confirmed_device(user) -> MfaDevice | None:
    return MfaDevice.objects.filter(user=user, confirmed_at__isnull=False).first()


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(user, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=MFA_ISSUER_NAME)


def verify_totp(secret: str, code) -> bool:
    code = str(code or "").replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=TOTP_VALID_WINDOW)


def _normalize_recovery_code(code) -> str:
    return str(code or "").strip().lower().replace("-", "").replace(" ", "")


def hash_recovery_code(code) -> str:
    # Keyed with SECRET_KEY, so a leaked database alone is not enough to brute-force the codes.
    return salted_hmac(
        "authentication.mfa.recovery_code", _normalize_recovery_code(code), algorithm="sha256"
    ).hexdigest()


@transaction.atomic
def generate_recovery_codes(user) -> list[str]:
    """Replace the user's recovery codes and return the new plain codes.

    The plain codes are not stored anywhere, so this return value is the only
    time they can be shown to the user.
    """
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(5)  # 10 hex characters
        codes.append(f"{raw[:5]}-{raw[5:]}")

    RecoveryCode.objects.filter(user=user).delete()
    RecoveryCode.objects.bulk_create(
        [RecoveryCode(user=user, code_hash=hash_recovery_code(code)) for code in codes]
    )
    return codes


def consume_recovery_code(user, code) -> bool:
    """Mark a matching unused recovery code as used. Each code works once."""
    if not _normalize_recovery_code(code):
        return False
    # One conditional UPDATE, so concurrent requests cannot both use the same code.
    updated = RecoveryCode.objects.filter(
        user=user,
        code_hash=hash_recovery_code(code),
        used_at__isnull=True,
    ).update(used_at=timezone.now())
    return updated == 1


def create_mfa_token(user) -> str:
    """Issued by LoginView after the password check; carries no API access."""
    payload = {"uid": user.pk, "v": getattr(user, "auth_token_version", 0)}
    return signing.dumps(payload, salt=MFA_TOKEN_SALT)


def read_mfa_token(token):
    try:
        payload = signing.loads(token, salt=MFA_TOKEN_SALT, max_age=MFA_TOKEN_MAX_AGE_SECONDS)
    except signing.BadSignature:  # also covers SignatureExpired
        return None
    user = get_user_model().objects.filter(pk=payload.get("uid"), is_active=True).first()
    # A password change bumps auth_token_version and invalidates outstanding passes.
    if user is None or getattr(user, "auth_token_version", 0) != payload.get("v"):
        return None
    return user


def mfa_token_from_request(request) -> str:
    """The mfa_token from the request body, or from the cookie set after Google sign-in."""
    return str(request.data.get("mfa_token") or request.COOKIES.get(MFA_TOKEN_COOKIE) or "")


def set_mfa_cookie(response, token: str) -> None:
    response.set_cookie(
        MFA_TOKEN_COOKIE,
        token,
        max_age=MFA_TOKEN_MAX_AGE_SECONDS,
        path=MFA_TOKEN_COOKIE_PATH,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite="Lax",
    )


def clear_mfa_cookie(response) -> None:
    response.delete_cookie(MFA_TOKEN_COOKIE, path=MFA_TOKEN_COOKIE_PATH, samesite="Lax")


def request_has_mfa(request) -> bool:
    token = getattr(request, "auth", None)
    return bool(token is not None and token.get("mfa_verified"))


def is_mfa_exempt_path(path: str) -> bool:
    return path.startswith(MFA_EXEMPT_PREFIXES)


def mfa_required_payload(user) -> dict:
    return {
        "error": "MFA verification required.",
        "errorCode": "MFA_REQUIRED",
        "mfa_enrollment_required": get_confirmed_device(user) is None,
    }

"""TOTP multi-factor authentication helpers for org admins (MED-326).

Shared by the enrollment endpoints, the login MFA step and the authorization
middleware. Secrets are stored encrypted (MfaDevice.set_secret); recovery codes
are stored only as keyed HMAC-SHA256 hashes, so the plain codes can be shown once.
"""

from __future__ import annotations

import secrets

import pyotp
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
# Short-lived, signed "password accepted" pass that only the MFA endpoints accept.
MFA_TOKEN_SALT = "authentication.mfa.login"
MFA_TOKEN_MAX_AGE_SECONDS = 5 * 60
# Requests an org admin can still make before passing MFA: the same account
# endpoints password rotation leaves open, plus the MFA endpoints themselves.
MFA_EXEMPT_PREFIXES = PasswordRotationMiddleware.EXEMPT_PREFIXES + ("/auth/mfa/",)


def requires_mfa(user) -> bool:
    """Org admins (level-2) must use MFA. Everyone else is unaffected."""
    from core.admin_utils import is_org_admin

    return is_org_admin(user) or has_public_level2_role(user)


def has_public_level2_role(user) -> bool:
    """A level-2 role for the user's current org that was written to the public schema.

    UserRole exists in public and in each org schema, and some code writes it to
    whichever schema is active (e.g. admin_views.assign_admin without org context).
    is_org_admin() only looks in the org schema, so MFA checks this one as well.
    """
    from access_control.models import UserRole
    from core.tenant_context import tenant_schema_context

    current_org = getattr(user, "current_organization", None) or getattr(user, "organization", None)
    if not current_org:
        return False
    with tenant_schema_context("public"):
        return UserRole.objects.filter(user=user, role__organization=current_org, role__level=2).exists()


def get_confirmed_device(user) -> MfaDevice | None:
    return MfaDevice.objects.filter(user=user, confirmed_at__isnull=False).first()


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(user, secret: str) -> str:
    """The otpauth:// URI an authenticator app reads from the QR code."""
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
    # A single conditional UPDATE, so two concurrent requests cannot both
    # use the same code.
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
    """Return the user for a valid, unexpired MFA token, otherwise None."""
    try:
        payload = signing.loads(token, salt=MFA_TOKEN_SALT, max_age=MFA_TOKEN_MAX_AGE_SECONDS)
    except signing.BadSignature:  # also covers SignatureExpired
        return None
    user = get_user_model().objects.filter(pk=payload.get("uid"), is_active=True).first()
    # A password change bumps auth_token_version and invalidates outstanding passes.
    if user is None or getattr(user, "auth_token_version", 0) != payload.get("v"):
        return None
    return user


def request_has_mfa(request) -> bool:
    """True when the request's JWT was issued after passing MFA (see build_login_response_data)."""
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

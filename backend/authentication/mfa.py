"""TOTP multi-factor authentication helpers for org admins (MED-326).

Shared by the enrollment endpoints, the login MFA step and the authorization
middleware. Secrets are stored encrypted (MfaDevice.set_secret); recovery codes
are stored only as SHA-256 hashes, so the plain codes can be shown once.
"""

from __future__ import annotations

import hashlib
import secrets

import pyotp
from django.db import transaction
from django.utils import timezone

from authentication.models import MfaDevice, RecoveryCode

MFA_ISSUER_NAME = "MediaJira"
# Accept the current 30-second step plus one step either side, to absorb
# small clock drift between the server and the authenticator app.
TOTP_VALID_WINDOW = 1
RECOVERY_CODE_COUNT = 10


def requires_mfa(user) -> bool:
    """Org admins (level-2) must use MFA. Everyone else is unaffected."""
    from core.admin_utils import is_org_admin

    return is_org_admin(user)


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
    return hashlib.sha256(_normalize_recovery_code(code).encode()).hexdigest()


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

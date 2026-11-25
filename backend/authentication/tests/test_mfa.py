from datetime import datetime, timezone as dt_timezone

import pyotp
import pytest
from django.utils import timezone
from freezegun import freeze_time

from authentication import mfa
from authentication.models import MfaDevice, RecoveryCode
from core.admin_utils import assign_org_admin
from core.models import CustomUser, Organization


pytestmark = pytest.mark.django_db

# 2026-01-01 00:00:15 UTC: 15 seconds into a 30-second TOTP step, so the
# neighbouring steps are exactly 30 seconds either side.
NOW = datetime(2026, 1, 1, 0, 0, 15, tzinfo=dt_timezone.utc)
NOW_TS = int(NOW.timestamp())


@pytest.fixture
def organization():
    return Organization.objects.create(name="MFA Org", email_domain="mfa-org.com")


def make_user(organization, email):
    return CustomUser.objects.create_user(
        username=email.split("@")[0],
        email=email,
        password="CurrentPassword123!",
        organization=organization,
        is_verified=True,
    )


@pytest.fixture
def org_admin(organization):
    user = make_user(organization, "admin@mfa-org.com")
    assign_org_admin(user, organization)
    return user


@pytest.fixture
def member(organization):
    return make_user(organization, "member@mfa-org.com")


# --- who needs MFA -----------------------------------------------------------

def test_org_admin_requires_mfa(org_admin):
    assert mfa.requires_mfa(org_admin) is True


def test_regular_member_does_not_require_mfa(member):
    assert mfa.requires_mfa(member) is False


def test_superuser_without_org_admin_role_does_not_require_mfa(organization):
    user = make_user(organization, "root@mfa-org.com")
    user.is_superuser = True
    user.save()

    assert mfa.requires_mfa(user) is False


# --- device and secret -------------------------------------------------------

def test_secret_is_stored_encrypted_and_round_trips(org_admin):
    secret = mfa.generate_secret()
    device = MfaDevice(user=org_admin)
    device.set_secret(secret)
    device.save()

    device.refresh_from_db()
    assert device.encrypted_secret != secret
    assert secret not in device.encrypted_secret
    assert device.get_secret() == secret


def test_only_confirmed_device_counts(org_admin):
    assert mfa.get_confirmed_device(org_admin) is None

    device = MfaDevice(user=org_admin)
    device.set_secret(mfa.generate_secret())
    device.save()
    assert mfa.get_confirmed_device(org_admin) is None

    device.confirmed_at = timezone.now()
    device.save()
    assert mfa.get_confirmed_device(org_admin) == device


def test_provisioning_uri_names_issuer_and_account(org_admin):
    secret = mfa.generate_secret()

    uri = mfa.provisioning_uri(org_admin, secret)

    assert uri.startswith("otpauth://totp/")
    assert "issuer=MediaJira" in uri
    assert "admin%40mfa-org.com" in uri
    assert f"secret={secret}" in uri


# --- TOTP 30-second window ---------------------------------------------------

@freeze_time(NOW)
@pytest.mark.parametrize("offset", [-30, 0, 30])
def test_totp_accepts_current_and_adjacent_steps(offset):
    secret = mfa.generate_secret()
    code = pyotp.TOTP(secret).at(NOW_TS + offset)

    assert mfa.verify_totp(secret, code) is True


@freeze_time(NOW)
@pytest.mark.parametrize("offset", [-60, 60])
def test_totp_rejects_codes_outside_the_window(offset):
    secret = mfa.generate_secret()
    code = pyotp.TOTP(secret).at(NOW_TS + offset)

    assert mfa.verify_totp(secret, code) is False


@freeze_time(NOW)
def test_totp_rejects_code_from_another_secret():
    code = pyotp.TOTP(mfa.generate_secret()).at(NOW_TS)

    assert mfa.verify_totp(mfa.generate_secret(), code) is False


@pytest.mark.parametrize("bad_code", ["", None, "12345", "1234567", "abcdef"])
def test_totp_rejects_malformed_codes(bad_code):
    assert mfa.verify_totp(mfa.generate_secret(), bad_code) is False


# --- recovery codes ----------------------------------------------------------

def test_recovery_codes_are_returned_once_and_stored_hashed(org_admin):
    codes = mfa.generate_recovery_codes(org_admin)

    assert len(codes) == mfa.RECOVERY_CODE_COUNT
    assert len(set(codes)) == mfa.RECOVERY_CODE_COUNT
    stored = list(RecoveryCode.objects.filter(user=org_admin).values_list("code_hash", flat=True))
    assert len(stored) == mfa.RECOVERY_CODE_COUNT
    for code in codes:
        assert code not in stored
        assert mfa.hash_recovery_code(code) in stored


def test_recovery_code_works_only_once(org_admin):
    code = mfa.generate_recovery_codes(org_admin)[0]

    assert mfa.consume_recovery_code(org_admin, code) is True
    assert mfa.consume_recovery_code(org_admin, code) is False


def test_recovery_code_ignores_case_dashes_and_spaces(org_admin):
    code = mfa.generate_recovery_codes(org_admin)[0]
    typed = f"  {code.replace('-', '').upper()} "

    assert mfa.consume_recovery_code(org_admin, typed) is True


def test_wrong_or_empty_recovery_code_is_rejected(org_admin):
    mfa.generate_recovery_codes(org_admin)

    assert mfa.consume_recovery_code(org_admin, "00000-00000") is False
    assert mfa.consume_recovery_code(org_admin, "") is False
    assert mfa.consume_recovery_code(org_admin, None) is False


def test_recovery_code_belongs_to_its_own_user(org_admin, member):
    code = mfa.generate_recovery_codes(org_admin)[0]

    assert mfa.consume_recovery_code(member, code) is False
    assert mfa.consume_recovery_code(org_admin, code) is True


def test_regenerating_recovery_codes_invalidates_old_ones(org_admin):
    old_code = mfa.generate_recovery_codes(org_admin)[0]

    mfa.generate_recovery_codes(org_admin)

    assert RecoveryCode.objects.filter(user=org_admin).count() == mfa.RECOVERY_CODE_COUNT
    assert mfa.consume_recovery_code(org_admin, old_code) is False

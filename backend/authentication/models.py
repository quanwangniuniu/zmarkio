import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.crypto import decrypt_token, encrypt_token


class AuthenticationLockout(models.Model):
    class Scope(models.TextChoices):
        IP = 'ip', 'IP address'
        USERNAME = 'username', 'Username'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.CharField(max_length=20, choices=Scope.choices)
    identifier = models.CharField(max_length=255, db_index=True)
    reason = models.CharField(max_length=255)
    failure_count = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(db_index=True)
    last_attempt_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'authentication_lockouts'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['scope', 'identifier', 'locked_until']),
            models.Index(fields=['scope', 'identifier', 'resolved_at']),
        ]

    def __str__(self):
        return f'{self.get_scope_display()} {self.identifier} locked until {self.locked_until.isoformat()}'

    @property
    def is_active(self):
        return self.resolved_at is None and self.locked_until > timezone.now()

    @property
    def seconds_remaining(self):
        remaining = int((self.locked_until - timezone.now()).total_seconds())
        return max(0, remaining)


class MfaDevice(models.Model):
    """A user's TOTP authenticator. One per user; confirmed_at is set once the
    first code from the authenticator app has been verified."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='mfa_device',
    )
    # core.crypto.encrypt_token output; the plain base32 secret is never stored.
    encrypted_secret = models.TextField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'authentication_mfa_devices'

    def __str__(self):
        state = 'confirmed' if self.is_confirmed else 'pending'
        return f'MFA device for user {self.user_id} ({state})'

    @property
    def is_confirmed(self):
        return self.confirmed_at is not None

    def set_secret(self, raw_secret):
        self.encrypted_secret = encrypt_token(raw_secret)

    def get_secret(self):
        return decrypt_token(self.encrypted_secret)


class RecoveryCode(models.Model):
    """A single-use MFA recovery code. Only a keyed HMAC-SHA256 hash is stored; the plain
    codes are returned to the user once, when they are generated."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='mfa_recovery_codes',
    )
    code_hash = models.CharField(max_length=64)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'authentication_mfa_recovery_codes'
        indexes = [
            models.Index(fields=['user', 'used_at']),
        ]

    def __str__(self):
        state = 'used' if self.used_at else 'unused'
        return f'Recovery code for user {self.user_id} ({state})'

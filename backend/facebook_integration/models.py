from django.conf import settings
from django.db import models

import logging

from .crypto import DecryptionError, decrypt_token, encrypt_token

logger = logging.getLogger(__name__)


class FacebookConnection(models.Model):
    """Per-user Meta OAuth connection.

    Stores the long-lived (~60 day) user access token returned by Facebook
    Login for Business. One row per authenticated user.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="facebook_connection",
    )

    # Identity returned by Graph API /me
    fb_user_id = models.CharField(max_length=64, db_index=True)
    fb_user_name = models.CharField(max_length=255, blank=True, default="")
    fb_email = models.EmailField(blank=True, null=True)

    # Business the user selected during consent (from /me/businesses)
    business_id = models.CharField(max_length=64, blank=True, default="")
    business_name = models.CharField(max_length=255, blank=True, default="")

    # Token (Fernet encrypted on write, decrypted on read via helpers)
    encrypted_access_token = models.TextField(blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)
    last_refreshed_at = models.DateTimeField(null=True, blank=True)

    # State
    is_active = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_error = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "facebook_connections"

    def __str__(self) -> str:
        return f"FacebookConnection(user={self.user_id}, fb_user={self.fb_user_name}, active={self.is_active})"

    def set_access_token(self, token: str | None) -> None:
        self.encrypted_access_token = encrypt_token(token) or ""

    def get_access_token(self) -> str | None:
        try:
            return decrypt_token(self.encrypted_access_token or None)
        except DecryptionError:
            logger.error(
                "FacebookConnection(user=%s): access token decryption failed — reconnect required.",
                self.user_id,
            )
            return None


class MetaAdAccount(models.Model):
    """Meta Ad Account the user has access to.

    Created when a FacebookConnection is established by querying
    `/me/businesses/<id>/owned_ad_accounts` and `/client_ad_accounts`.
    """

    connection = models.ForeignKey(
        FacebookConnection,
        on_delete=models.CASCADE,
        related_name="ad_accounts",
    )
    meta_account_id = models.CharField(max_length=64, db_index=True)  # without the 'act_' prefix
    name = models.CharField(max_length=255, blank=True, default="")
    currency = models.CharField(max_length=8, blank=True, default="")
    timezone_name = models.CharField(max_length=64, blank=True, default="")
    account_status = models.IntegerField(null=True, blank=True)  # 1 active, 2 disabled, etc.
    business_id = models.CharField(max_length=64, blank=True, default="")
    is_owned = models.BooleanField(default=False)  # owned vs client access

    # Link to a MediaJira project (set by user later in UI)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="linked_meta_accounts",
        # Project lives in the organization schema; a public FK can never match.
        db_constraint=False,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("connection", "meta_account_id")]
        db_table = "meta_ad_accounts"

    def __str__(self) -> str:
        return f"MetaAdAccount(act_{self.meta_account_id}, {self.name})"

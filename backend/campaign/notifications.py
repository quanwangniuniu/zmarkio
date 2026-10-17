"""
In-app notifications for campaign platform sync events.
"""

from __future__ import annotations

from notifications.action_urls import campaign_action_url
from notifications.models import NotificationCategory, NotificationEventType
from notifications.services import create_notification

CHANGE_SYNC_AUTH_ERROR = "campaign_sync_auth_error"


def notify_campaign_sync_auth_error(campaign, *, ad_account) -> None:
    """Tell the campaign owner that a linked Meta account needs reconnecting."""
    connector = ad_account.connection.user
    create_notification(
        recipient_id=campaign.owner_id,
        actor_id=None,
        category=NotificationCategory.INTEGRATIONS,
        event_type=NotificationEventType.ACCOUNT_PERMISSION,
        title=f"Reconnect Meta for {campaign.name}",
        body=(
            f"Meta authorization for {ad_account.name or 'the linked ad account'} has expired "
            f"or been revoked, so campaign metrics may be out of date. "
            f"{connector.get_full_name() or connector.username} must reconnect the account."
        ),
        related_object_type="campaign",
        related_object_id=str(campaign.id),
        action_url=campaign_action_url(campaign.slug),
        metadata={
            "change_type": CHANGE_SYNC_AUTH_ERROR,
            "project_id": campaign.project_id,
            "campaign_slug": campaign.slug,
            "ad_account_id": ad_account.pk,
        },
    )

"""Celery tasks for Meta ads sync.

Hourly full sync is the default cadence per kickoff M5 decision. 15min sync
for realtime pacing/fatigue is implemented but left unscheduled until L2.
"""

import datetime as _dt
import logging

from celery import shared_task
from django.utils import timezone

from facebook_integration.models import FacebookConnection, MetaAdAccount

from .services import sync_ad_account


logger = logging.getLogger(__name__)


# Window in which a still-running MetaSyncRun blocks the daily fan-out from
# dispatching another sync for the same account. Recent live runs land at
# around 100 seconds; 10 minutes leaves plenty of headroom while keeping a
# stuck-running row from wedging cron forever.
LOCK_WINDOW_MINUTES = 10


@shared_task
def sync_all_meta_connections(kind: str = "hourly", days: int = 30) -> dict:
    """Iterate every active FacebookConnection and refresh each ad account."""
    summary = {"connections": 0, "ad_accounts": 0, "errors": 0}
    for connection in FacebookConnection.objects.filter(is_active=True):
        token = connection.get_access_token()
        if not token:
            continue
        summary["connections"] += 1
        for ad_account in MetaAdAccount.objects.filter(connection=connection):
            summary["ad_accounts"] += 1
            try:
                run = sync_ad_account(ad_account, token, days=days, kind=kind)
                if run.status == "error":
                    summary["errors"] += 1
            except Exception:  # pragma: no cover - defensive
                logger.exception("sync_ad_account crashed for %s", ad_account.meta_account_id)
                summary["errors"] += 1
        connection.last_synced_at = timezone.now()
        connection.save(update_fields=["last_synced_at", "updated_at"])
    return summary


@shared_task
def sync_recent_meta(days: int = 2) -> dict:
    """Lightweight 15-minute sync that only refreshes the trailing 2 days of insights."""
    return sync_all_meta_connections(kind="15min", days=days)


@shared_task
def sync_single_ad_account(ad_account_id: int, days: int = 30) -> dict:
    """Manual sync trigger used by the UI Refresh button."""
    try:
        ad_account = MetaAdAccount.objects.get(pk=ad_account_id)
    except MetaAdAccount.DoesNotExist:
        return {"error": "not_found"}
    token = ad_account.connection.get_access_token()
    if not token:
        return {"error": "no_token"}
    run = sync_ad_account(ad_account, token, days=days, kind="manual")
    return {"status": run.status, "level_counts": run.level_counts, "error": run.error_message}


@shared_task
def sync_all_active_ad_accounts() -> dict:
    """Fan out a per-account sync to every active ad account.

    Active means the parent FacebookConnection is active and the account is
    not flagged disabled on Meta's side (account_status 2 or 3). An account
    with account_status null is treated as active because legacy rows may
    pre-date the Meta enum field.

    Skips any account that already has a MetaSyncRun row in status running
    inside the lock window, so a daily fan-out colliding with a manual
    refresh does not start a parallel run for the same account.
    """
    from .models import MetaSyncRun

    lock_cutoff = timezone.now() - _dt.timedelta(minutes=LOCK_WINDOW_MINUTES)
    in_flight_account_ids = set(
        MetaSyncRun.objects.filter(
            status="running",
            started_at__gte=lock_cutoff,
        ).values_list("ad_account_id", flat=True)
    )

    candidates = (
        MetaAdAccount.objects.filter(connection__is_active=True)
        .exclude(account_status__in=[2, 3])
    )

    dispatched = 0
    skipped_locked = 0
    for account in candidates:
        if account.id in in_flight_account_ids:
            skipped_locked += 1
            logger.info(
                "sync_all_active_ad_accounts: skipping %s (sync already in flight)",
                account.meta_account_id,
            )
            continue
        sync_single_ad_account.delay(ad_account_id=account.id)
        dispatched += 1

    return {"dispatched": dispatched, "skipped_locked": skipped_locked}

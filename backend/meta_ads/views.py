"""Read-only endpoints for the Meta ads data layer.

Write paths (sync) live in `services.py` and are driven by Celery tasks; an
on-demand sync is exposed through `facebook_integration.FacebookSyncView` and
`MetaSyncRunTriggerView` below.
"""

import csv
import datetime as _dt
import secrets as _secrets
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import IntegerField, OuterRef, Subquery, Sum
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404

from core.slug_mixins import resolve_pk_for  # resolve a detail id that may be a slug or a legacy pk
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Project
from facebook_integration.access import (
    get_accessible_meta_ad_account_or_404,
    get_accessible_meta_ad_accounts,
    user_can_sync_meta_ad_account,
)
from facebook_integration.models import MetaAdAccount
from spreadsheet.models import (
    Cell,
    CellValueType,
    ComputedCellType,
    Sheet,
    SheetColumn,
    SheetRow,
)
from spreadsheet.services import SheetService, SpreadsheetService

from .models import (
    MetaAd,
    MetaAdCreative,
    MetaAdSet,
    MetaCampaign,
    MetaInsightDaily,
    MetaSyncRun,
)
from .serializers import (
    MetaAdSerializer,
    MetaCampaignSerializer,
    MetaInsightDailySerializer,
    MetaSyncRunSerializer,
)
from .services import CreativePreviewError, get_creative_preview
from .tasks import sync_all_active_ad_accounts, sync_single_ad_account


# Helpers ---------------------------------------------------------------------


def _user_ad_account(request, ad_account_id: int) -> MetaAdAccount:
    return get_accessible_meta_ad_account_or_404(request.user, ad_account_id)


def _accessible_ad_accounts_for_user(user):
    return get_accessible_meta_ad_accounts(user).values("id")


# Views -----------------------------------------------------------------------


class MetaCampaignListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, ad_account_id: int):
        ad_account = _user_ad_account(request, ad_account_id)
        qs = MetaCampaign.objects.filter(ad_account=ad_account).order_by("-updated_at")
        return Response(MetaCampaignSerializer(qs, many=True).data)


def _build_campaign_perf_queryset(
    *,
    ad_account: MetaAdAccount,
    since: _dt.date,
    today: _dt.date,
    ids: list[int] | None = None,
):
    """Annotated, scoped queryset for campaign-level performance reads.

    Both the JSON perf endpoint and the new CSV / Spreadsheet exports call
    this. When `ids` is non-empty the returned queryset is restricted to
    those campaign primary keys; otherwise every campaign in the account
    is returned and the caller filters/sorts as needed.
    """
    window_q = models.Q(
        adsets__ads__insights_daily__date__gte=since,
        adsets__ads__insights_daily__date__lte=today,
    )
    qs = MetaCampaign.objects.filter(ad_account=ad_account).annotate(
        total_spend=Sum("adsets__ads__insights_daily__spend", filter=window_q),
        total_impressions=Sum(
            "adsets__ads__insights_daily__impressions", filter=window_q
        ),
        total_clicks=Sum("adsets__ads__insights_daily__clicks", filter=window_q),
        total_leads=Sum("adsets__ads__insights_daily__leads", filter=window_q),
        total_purchases=Sum(
            "adsets__ads__insights_daily__purchases", filter=window_q
        ),
        total_revenue=Sum("adsets__ads__insights_daily__revenue", filter=window_q),
    )
    if ids:
        qs = qs.filter(id__in=ids)
    return qs


class MetaCampaignPerformanceView(APIView):
    """Per-campaign aggregates over a trailing window.

    Returns one row per campaign with totals across its adsets/ads, so the
    frontend can render a leaderboard table without joining on the client.
    """

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def get(self, request, ad_account_id: int):
        ad_account = _user_ad_account(request, ad_account_id)
        try:
            days = int(request.query_params.get("days", 28))
        except ValueError:
            days = 28
        if days not in self.ALLOWED_DAYS:
            days = 28

        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)

        qs = _build_campaign_perf_queryset(
            ad_account=ad_account, since=since, today=today
        )

        rows = []
        for camp in qs:
            spend = camp.total_spend or Decimal("0")
            rev = camp.total_revenue or Decimal("0")
            imp = camp.total_impressions or 0
            clicks = camp.total_clicks or 0
            leads = camp.total_leads or 0
            purchases = camp.total_purchases or 0
            rows.append({
                "id": camp.id,
                "slug": camp.slug,
                "meta_campaign_id": camp.meta_campaign_id,
                "name": camp.name,
                "objective": camp.objective,
                "effective_status": camp.effective_status,
                "daily_budget_cents": camp.daily_budget_cents,
                "lifetime_budget_cents": camp.lifetime_budget_cents,
                "spend": str(spend),
                "impressions": imp,
                "clicks": clicks,
                "leads": leads,
                "purchases": purchases,
                "revenue": str(rev),
                "ctr": str(_safe_ratio(clicks, imp) * Decimal("100")),
                "cpc": str(_safe_ratio(spend, clicks)),
                "cpm": str(_safe_ratio(spend, imp) * Decimal("1000")),
                "cpl": str(_safe_ratio(spend, leads)),
                "cpa": str(_safe_ratio(spend, purchases)),
                "roas": str(_safe_ratio(rev, spend)),
            })
        rows.sort(key=lambda r: Decimal(r["spend"]), reverse=True)
        return Response({
            "ad_account_id": ad_account.id,
            "currency": ad_account.currency,
            "days": days,
            "window": {"since": since.isoformat(), "until": today.isoformat()},
            "campaigns": rows,
        })


class MetaAdListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, ad_account_id: int):
        ad_account = _user_ad_account(request, ad_account_id)
        qs = (
            MetaAd.objects.filter(adset__campaign__ad_account=ad_account)
            .select_related("adset", "creative")
            .order_by("-updated_at")
        )
        return Response(MetaAdSerializer(qs, many=True).data)


class MetaInsightListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, ad_account_id: int):
        ad_account = _user_ad_account(request, ad_account_id)
        since = _parse_date(request.query_params.get("since"))
        until = _parse_date(request.query_params.get("until"))
        qs = MetaInsightDaily.objects.filter(
            ad__adset__campaign__ad_account=ad_account
        )
        if since:
            qs = qs.filter(date__gte=since)
        if until:
            qs = qs.filter(date__lte=until)
        qs = qs.order_by("-date", "ad_id")[:10000]
        return Response(MetaInsightDailySerializer(qs, many=True).data)


class MetaSummaryView(APIView):
    """Aggregate KPIs + day-by-day timeseries for a given trailing window.

    Query: ?ad_account=<id>&days=7 (allowed: 1, 2, 3, 7, 14, 28).
    """

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def get(self, request):
        ad_account_id = request.query_params.get("ad_account")
        if not ad_account_id:
            return Response({"detail": "ad_account query parameter is required."}, status=400)
        try:
            ad_account = _user_ad_account(request, int(ad_account_id))
        except ValueError:
            return Response({"detail": "Ad account not found."}, status=404)

        try:
            days = int(request.query_params.get("days", 7))
        except ValueError:
            days = 7
        if days not in self.ALLOWED_DAYS:
            days = 7

        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)
        qs = MetaInsightDaily.objects.filter(
            ad__adset__campaign__ad_account=ad_account,
            date__gte=since,
            date__lte=today,
        )

        aggregates = qs.aggregate(
            spend=Sum("spend"),
            impressions=Sum("impressions"),
            clicks=Sum("clicks"),
            reach=Sum("reach"),
            leads=Sum("leads"),
            calls=Sum("calls"),
            purchases=Sum("purchases"),
            revenue=Sum("revenue"),
        )
        aggregates = {k: (v if v is not None else 0) for k, v in aggregates.items()}
        aggregates["ctr"] = _safe_ratio(aggregates["clicks"], aggregates["impressions"])
        aggregates["cpc"] = _safe_ratio(aggregates["spend"], aggregates["clicks"])
        aggregates["cpm"] = _safe_ratio(aggregates["spend"], aggregates["impressions"]) * Decimal("1000")
        aggregates["cpl"] = _safe_ratio(aggregates["spend"], aggregates["leads"])
        aggregates["cpcall"] = _safe_ratio(aggregates["spend"], aggregates["calls"])
        aggregates["roas"] = _safe_ratio(aggregates["revenue"], aggregates["spend"])

        timeseries_map: dict[_dt.date, dict[str, Decimal]] = defaultdict(
            lambda: {"spend": Decimal("0"), "impressions": 0, "clicks": 0, "leads": 0, "calls": 0, "purchases": 0, "revenue": Decimal("0")}
        )
        for row in qs.values(
            "date", "spend", "impressions", "clicks", "leads", "calls", "purchases", "revenue"
        ):
            entry = timeseries_map[row["date"]]
            entry["spend"] += row["spend"] or Decimal("0")
            entry["impressions"] += row["impressions"] or 0
            entry["clicks"] += row["clicks"] or 0
            entry["leads"] += row["leads"] or 0
            entry["calls"] += row["calls"] or 0
            entry["purchases"] += row["purchases"] or 0
            entry["revenue"] += row["revenue"] or Decimal("0")

        timeseries = []
        current = since
        while current <= today:
            entry = timeseries_map.get(current, {
                "spend": Decimal("0"), "impressions": 0, "clicks": 0, "leads": 0, "calls": 0, "purchases": 0, "revenue": Decimal("0"),
            })
            timeseries.append({
                "date": current.isoformat(),
                "spend": str(entry["spend"]),
                "impressions": int(entry["impressions"]),
                "clicks": int(entry["clicks"]),
                "leads": int(entry["leads"]),
                "calls": int(entry["calls"]),
                "purchases": int(entry["purchases"]),
                "revenue": str(entry["revenue"]),
            })
            current += _dt.timedelta(days=1)

        return Response({
            "ad_account_id": ad_account.id,
            "currency": ad_account.currency,
            "days": days,
            "window": {"since": since.isoformat(), "until": today.isoformat()},
            "aggregates": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in aggregates.items()},
            "timeseries": timeseries,
        })


class MetaSyncRunTriggerView(APIView):
    """POST endpoint to kick off a manual sync for one ad account.

    Fire-and-forget: the Celery task creates a `MetaSyncRun` row with
    status=running immediately, then fills in counts/status when done.
    Clients poll `/ad_accounts/<id>/sync_runs/` to observe progress.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, ad_account_id: int):
        ad_account = _user_ad_account(request, ad_account_id)
        if not user_can_sync_meta_ad_account(request.user, ad_account):
            return Response(
                {"detail": "You do not have permission to sync this ad account."},
                status=403,
            )
        async_result = sync_single_ad_account.delay(ad_account.id)
        return Response(
            {
                "status": "queued",
                "task_id": async_result.id,
                "ad_account_id": ad_account.id,
                "poll_url": f"/api/meta_ads/ad_accounts/{ad_account.id}/sync_runs/",
            },
            status=202,
        )


class MetaAdTriggerDailySyncAllView(APIView):
    """Server-to-server endpoint that fires the daily sync fan-out.

    Authenticated by a shared secret carried in the X-Internal-Cron-Secret
    header and compared in constant time against the INTERNAL_CRON_SECRET
    setting. Fail-closed: when the server-side secret is empty the endpoint
    rejects every call, even one with an empty header. Designed for
    platform-native cron (K8s CronJob, Render Cron, system crontab) so
    production does not need a celery-beat sidecar.

    On success dispatches sync_all_active_ad_accounts via Celery and returns
    the resulting task id for ops debuggability. Duplicate dispatches inside
    the in-flight lock window are absorbed by sync_all_active_ad_accounts
    itself, so this endpoint does not need its own idempotency check.
    """

    permission_classes = []
    authentication_classes = []

    def post(self, request):
        expected = getattr(settings, "INTERNAL_CRON_SECRET", "") or ""
        provided = request.META.get("HTTP_X_INTERNAL_CRON_SECRET", "") or ""
        if not expected or not _secrets.compare_digest(expected, provided):
            return Response({"detail": "invalid secret"}, status=401)
        try:
            async_result = sync_all_active_ad_accounts.delay()
        except Exception:
            return Response({"detail": "broker unreachable"}, status=503)
        return Response(
            {"dispatched_task_id": async_result.id},
            status=202,
        )


class MetaSyncRunListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, ad_account_id: int):
        ad_account = _user_ad_account(request, ad_account_id)
        qs = MetaSyncRun.objects.filter(ad_account=ad_account)[:50]
        return Response(MetaSyncRunSerializer(qs, many=True).data)


def _build_creative_perf_queryset(
    *,
    ad_account: MetaAdAccount,
    since: _dt.date,
    today: _dt.date,
    ids: list[int] | None = None,
    include_inactive: bool = False,
    include_shared_creatives: bool = False,
    min_impressions: int = 0,
    min_spend: Decimal = Decimal("0"),
    min_days_with_data: int = 0,
):
    """Annotated, scoped, filtered queryset for creative-level perf reads.

    Both the JSON leaderboard endpoint and the new CSV / Spreadsheet exports
    call this. When `ids` is non-empty the activity / shared / data-quality
    filters are bypassed because the caller has explicitly picked those
    creatives. Orphan creatives (zero ad references) are always excluded
    even in `ids` mode because they have no measurable performance.
    """
    window_q = models.Q(
        ads__insights_daily__date__gte=since,
        ads__insights_daily__date__lte=today,
    )
    qs = MetaAdCreative.objects.filter(ad_account=ad_account).annotate(
        total_spend=Sum("ads__insights_daily__spend", filter=window_q),
        total_impressions=Sum(
            "ads__insights_daily__impressions", filter=window_q
        ),
        total_clicks=Sum("ads__insights_daily__clicks", filter=window_q),
        total_leads=Sum("ads__insights_daily__leads", filter=window_q),
        total_calls=Sum("ads__insights_daily__calls", filter=window_q),
        total_purchases=Sum(
            "ads__insights_daily__purchases", filter=window_q
        ),
        total_messages=Sum("ads__insights_daily__messages", filter=window_q),
        total_revenue=Sum("ads__insights_daily__revenue", filter=window_q),
        total_video_p25=Sum(
            "ads__insights_daily__video_p25", filter=window_q
        ),
        total_video_p50=Sum(
            "ads__insights_daily__video_p50", filter=window_q
        ),
        total_video_p75=Sum(
            "ads__insights_daily__video_p75", filter=window_q
        ),
        total_video_p100=Sum(
            "ads__insights_daily__video_p100", filter=window_q
        ),
        total_video_3sec=Sum(
            "ads__insights_daily__video_3sec_count", filter=window_q
        ),
        total_lpv=Sum("ads__insights_daily__lpv_count", filter=window_q),
        total_comments=Sum(
            "ads__insights_daily__comment_count", filter=window_q
        ),
        n_days_with_data=models.Count(
            "ads__insights_daily",
            filter=window_q
            & models.Q(ads__insights_daily__impressions__gt=0),
        ),
        ad_count=models.Count("ads", distinct=True),
    ).filter(ad_count__gte=1)

    if ids:
        qs = qs.filter(id__in=ids)
    else:
        if not include_shared_creatives:
            qs = qs.filter(ad_count=1)
        if not include_inactive:
            qs = qs.filter(total_impressions__gt=0)
        if min_impressions > 0:
            qs = qs.filter(total_impressions__gte=min_impressions)
        if min_spend > 0:
            qs = qs.filter(total_spend__gte=min_spend)
        if min_days_with_data > 0:
            qs = qs.filter(n_days_with_data__gte=min_days_with_data)
    return qs


class MetaCreativePerformanceView(APIView):
    """Creative-level leaderboard with hook/hold rates + core perf.

    Aggregates insights across every ad linked to the creative, so the
    frontend gets one row per creative with performance already reduced.

    Filters
    -------
    `min_impressions` (int, default 0), `min_spend` (decimal, default 0),
    `min_events` (int, default 0), `min_days_with_data` (int, default 0):
    drop low-data creatives so the leaderboard is not polluted by paused
    or under-delivered ads. `min_spend` is in the ad account's native
    currency. `min_events` sums leads + calls + purchases + messages
    across linked ads. `min_days_with_data` counts linked-ad x date pairs
    where impressions > 0 (so a creative referenced by N ads delivering
    on the same date contributes N to this count, not 1).

    `include_inactive` (bool, default false): when false, creatives with
    zero linked-insight impressions in the window are excluded.

    `include_shared_creatives` (bool, default false): when false, only
    creatives referenced by exactly 1 ad are returned (the 1:1 cohort).
    When true, creatives referenced by >= 1 ad are returned (1:1 + N:M).
    Orphan creatives (0 ad references) are always excluded; there is no
    toggle for them.

    Each row carries `is_in_learning` (events/week < 50) plus the raw
    `total_events`, `days_with_data`, `lpv_count`, `comment_count`,
    `video_3sec_count` so the frontend can grey out low-confidence rows.
    `is_in_learning` is null when `days < 7`.
    """

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def get(self, request, ad_account_id: int):
        ad_account = _user_ad_account(request, ad_account_id)
        days = _normalize_days(request.query_params.get("days"), self.ALLOWED_DAYS, default=28)
        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)

        min_impressions = _parse_qp_int(request, "min_impressions")
        min_events = _parse_qp_int(request, "min_events")
        min_days_with_data = _parse_qp_int(request, "min_days_with_data")
        min_spend = _parse_qp_decimal(request, "min_spend")
        include_inactive = _parse_qp_bool(request, "include_inactive", default=False)
        include_shared_creatives = _parse_qp_bool(
            request, "include_shared_creatives", default=False
        )

        qs = _build_creative_perf_queryset(
            ad_account=ad_account,
            since=since,
            today=today,
            include_inactive=include_inactive,
            include_shared_creatives=include_shared_creatives,
            min_impressions=min_impressions,
            min_spend=min_spend,
            min_days_with_data=min_days_with_data,
        )

        rows = []
        for cr in qs:
            spend = cr.total_spend or Decimal("0")
            rev = cr.total_revenue or Decimal("0")
            imp = cr.total_impressions or 0
            clicks = cr.total_clicks or 0
            leads = cr.total_leads or 0
            calls = cr.total_calls or 0
            purchases = cr.total_purchases or 0
            messages = cr.total_messages or 0
            p25 = cr.total_video_p25 or 0
            p75 = cr.total_video_p75 or 0
            p100 = cr.total_video_p100 or 0
            v3 = cr.total_video_3sec or 0
            lpv = cr.total_lpv or 0
            comments = cr.total_comments or 0
            days_with_data = cr.n_days_with_data or 0
            total_events = leads + calls + purchases + messages
            if days < 7:
                is_in_learning = None
            else:
                is_in_learning = (total_events * 7) < (50 * days)
            hook_rate = _safe_ratio(p25, imp) * Decimal("100")
            hold_rate = _safe_ratio(p75, p25) * Decimal("100")
            completion_rate = _safe_ratio(p100, p25) * Decimal("100")
            rows.append({
                "id": cr.id,
                "slug": cr.slug,
                "meta_creative_id": cr.meta_creative_id,
                "name": cr.name,
                "title": cr.title,
                "body": cr.body,
                "thumbnail_url": cr.thumbnail_url,
                "image_url": cr.image_url,
                "video_id": cr.video_id,
                "object_type": cr.object_type,
                "call_to_action_type": cr.call_to_action_type,
                "spend": str(spend),
                "impressions": imp,
                "clicks": clicks,
                "leads": leads,
                "calls": calls,
                "purchases": purchases,
                "messages": messages,
                "revenue": str(rev),
                "ctr": str(_safe_ratio(clicks, imp) * Decimal("100")),
                "cpc": str(_safe_ratio(spend, clicks)),
                "cpl": str(_safe_ratio(spend, leads)),
                "cpa": str(_safe_ratio(spend, purchases)),
                "roas": str(_safe_ratio(rev, spend)),
                "video_p25": p25,
                "video_p75": p75,
                "video_p100": p100,
                "hook_rate": str(hook_rate),
                "hook_rate_strict": str(_safe_ratio(v3, imp) * Decimal("100")),
                "hold_rate": str(hold_rate),
                "completion_rate": str(completion_rate),
                "video_3sec_count": v3,
                "lpv_count": lpv,
                "cost_per_lpv": str(_safe_ratio(spend, lpv)),
                "comment_count": comments,
                "cost_per_comment": str(_safe_ratio(spend, comments)),
                "total_events": total_events,
                "days_with_data": days_with_data,
                "is_in_learning": is_in_learning,
                "ad_count": cr.ad_count,
            })
        if min_events > 0:
            rows = [r for r in rows if r["total_events"] >= min_events]
        rows.sort(key=lambda r: Decimal(r["spend"]), reverse=True)
        return Response({
            "ad_account_id": ad_account.id,
            "currency": ad_account.currency,
            "days": days,
            "window": {"since": since.isoformat(), "until": today.isoformat()},
            "filters": {
                "min_impressions": min_impressions,
                "min_spend": str(min_spend),
                "min_events": min_events,
                "min_days_with_data": min_days_with_data,
                "include_inactive": include_inactive,
                "include_shared_creatives": include_shared_creatives,
            },
            "creatives": rows,
        })


class MetaAdSetPerformanceView(APIView):
    """AdSet-level leaderboard — the missing middle layer of the hierarchy.

    Accepts an optional `campaign_id` query param so the frontend can
    narrow the list to just the adsets under a specific campaign (used
    when expanding a campaign row in the Overview table).
    """

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def get(self, request, ad_account_id: int):
        ad_account = _user_ad_account(request, ad_account_id)
        days = _normalize_days(request.query_params.get("days"), self.ALLOWED_DAYS, default=28)
        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)

        qs = MetaAdSet.objects.filter(
            campaign__ad_account=ad_account
        ).select_related("campaign").annotate(
            total_spend=Sum(
                "ads__insights_daily__spend",
                filter=models.Q(
                    ads__insights_daily__date__gte=since,
                    ads__insights_daily__date__lte=today,
                ),
            ),
            total_impressions=Sum(
                "ads__insights_daily__impressions",
                filter=models.Q(
                    ads__insights_daily__date__gte=since,
                    ads__insights_daily__date__lte=today,
                ),
            ),
            total_clicks=Sum(
                "ads__insights_daily__clicks",
                filter=models.Q(
                    ads__insights_daily__date__gte=since,
                    ads__insights_daily__date__lte=today,
                ),
            ),
            total_leads=Sum(
                "ads__insights_daily__leads",
                filter=models.Q(
                    ads__insights_daily__date__gte=since,
                    ads__insights_daily__date__lte=today,
                ),
            ),
            total_purchases=Sum(
                "ads__insights_daily__purchases",
                filter=models.Q(
                    ads__insights_daily__date__gte=since,
                    ads__insights_daily__date__lte=today,
                ),
            ),
            total_revenue=Sum(
                "ads__insights_daily__revenue",
                filter=models.Q(
                    ads__insights_daily__date__gte=since,
                    ads__insights_daily__date__lte=today,
                ),
            ),
        )

        campaign_id = request.query_params.get("campaign_id")
        if campaign_id:
            try:
                qs = qs.filter(campaign_id=int(campaign_id))
            except ValueError:
                return Response(
                    {"detail": "campaign_id must be an integer."}, status=400
                )

        rows = []
        for adset in qs:
            spend = adset.total_spend or Decimal("0")
            rev = adset.total_revenue or Decimal("0")
            imp = adset.total_impressions or 0
            clicks = adset.total_clicks or 0
            leads = adset.total_leads or 0
            purchases = adset.total_purchases or 0
            rows.append({
                "id": adset.id,
                "slug": adset.slug,
                "meta_adset_id": adset.meta_adset_id,
                "name": adset.name,
                "effective_status": adset.effective_status,
                "optimization_goal": adset.optimization_goal,
                "daily_budget_cents": adset.daily_budget_cents,
                "lifetime_budget_cents": adset.lifetime_budget_cents,
                "campaign_id": adset.campaign_id,
                "campaign_name": adset.campaign.name if adset.campaign else "",
                "spend": str(spend),
                "impressions": imp,
                "clicks": clicks,
                "leads": leads,
                "purchases": purchases,
                "revenue": str(rev),
                "ctr": str(_safe_ratio(clicks, imp) * Decimal("100")),
                "cpc": str(_safe_ratio(spend, clicks)),
                "cpl": str(_safe_ratio(spend, leads)),
                "cpa": str(_safe_ratio(spend, purchases)),
                "roas": str(_safe_ratio(rev, spend)),
            })
        rows.sort(key=lambda r: Decimal(r["spend"]), reverse=True)
        return Response({
            "ad_account_id": ad_account.id,
            "currency": ad_account.currency,
            "days": days,
            "window": {"since": since.isoformat(), "until": today.isoformat()},
            "campaign_id": int(campaign_id) if campaign_id else None,
            "adsets": rows,
        })


def _build_ad_perf_queryset(
    *,
    ad_account: MetaAdAccount,
    days: int,
    since: _dt.date,
    today: _dt.date,
    ids: list[int],
    include_inactive: bool,
    min_impressions: int,
    min_spend: Decimal,
    min_days_with_data: int,
    campaign_id: int | None = None,
    adset_id: int | None = None,
):
    """Annotated, scoped, filtered queryset for ad-level performance reads.

    Shared by the JSON perf endpoint and the CSV export endpoint. Callers own
    row-level construction, the min_events filter (events are summed in-row,
    not annotated), and the final ordering pass.

    Filtering rules mirror the JSON contract: when `ids` is non-empty the
    activity and data-quality filters are bypassed because the caller picked
    those rows on purpose. Account scoping on the base queryset still drops
    ids that belong to a foreign account, so cross-tenant leaks are not
    possible even in the explicit-id branch.
    """
    base_qs = MetaAd.objects.filter(adset__campaign__ad_account=ad_account)
    if campaign_id is not None:
        base_qs = base_qs.filter(adset__campaign_id=campaign_id)
    if adset_id is not None:
        base_qs = base_qs.filter(adset_id=adset_id)

    window_q = models.Q(
        insights_daily__date__gte=since,
        insights_daily__date__lte=today,
    )

    qs = (
        base_qs
        .select_related("adset", "adset__campaign", "creative")
        .annotate(
            total_spend=Sum("insights_daily__spend", filter=window_q),
            total_impressions=Sum("insights_daily__impressions", filter=window_q),
            # Reach is summed across the daily rows in the window. Daily reach
            # counts unique people per day, so an ad that ran for several days
            # has the same person counted once per day they were reached. The
            # summed total therefore over-states the true window-unique reach.
            # Frequency derived in-row from total_impressions / total_reach
            # carries the same caveat.
            total_reach=Sum("insights_daily__reach", filter=window_q),
            total_clicks=Sum("insights_daily__clicks", filter=window_q),
            total_leads=Sum("insights_daily__leads", filter=window_q),
            total_calls=Sum("insights_daily__calls", filter=window_q),
            total_purchases=Sum("insights_daily__purchases", filter=window_q),
            total_messages=Sum("insights_daily__messages", filter=window_q),
            total_revenue=Sum("insights_daily__revenue", filter=window_q),
            total_video_p25=Sum("insights_daily__video_p25", filter=window_q),
            total_video_p75=Sum("insights_daily__video_p75", filter=window_q),
            total_video_p100=Sum("insights_daily__video_p100", filter=window_q),
            total_video_3sec=Sum("insights_daily__video_3sec_count", filter=window_q),
            total_lpv=Sum("insights_daily__lpv_count", filter=window_q),
            total_comments=Sum("insights_daily__comment_count", filter=window_q),
            n_days_with_data=models.Count(
                "insights_daily",
                filter=window_q & models.Q(insights_daily__impressions__gt=0),
            ),
        )
    )

    if ids:
        qs = qs.filter(id__in=ids)
    else:
        if not include_inactive:
            qs = qs.filter(total_impressions__gt=0)
        if min_impressions > 0:
            qs = qs.filter(total_impressions__gte=min_impressions)
        if min_spend > 0:
            qs = qs.filter(total_spend__gte=min_spend)
        if min_days_with_data > 0:
            qs = qs.filter(n_days_with_data__gte=min_days_with_data)

    return qs


class MetaAdPerformanceView(APIView):
    """Ad-level leaderboard used by the drill-down tab.

    Returns one row per ad with rolling-window metrics plus the attached
    creative summary — so the frontend picker can show thumbnails and
    core perf in a single list without a second round-trip.

    Filters
    -------
    `campaign_id`, `adset_id` (int): narrow the list to ads under a specific
    campaign or ad set.

    `min_impressions` (int, default 0), `min_spend` (decimal, default 0),
    `min_events` (int, default 0), `min_days_with_data` (int, default 0):
    drop low-data rows so ranking isn't polluted by paused / under-delivered
    ads. `min_spend` is in the ad account's native currency — cross-account
    comparisons should normalise on the client. `min_events` is the sum of
    leads + calls + purchases + messages within the window.
    `min_days_with_data` counts days where impressions > 0.

    `include_inactive` (bool, default false): when false, ads with zero
    impressions in the window are excluded so paused ads don't pollute
    rankings. Pass `?include_inactive=true` to return them anyway.

    Each row carries `is_in_learning` (events/week < 50) plus the raw
    `total_events`, `days_with_data`, `lpv_count`, `comment_count`, and
    `video_3sec_count` so the frontend can grey out low-confidence entries.
    `is_in_learning` is null when `days < 7` (window too short to judge).
    """

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def get(self, request, ad_account_id: int):
        ad_account = _user_ad_account(request, ad_account_id)
        days = _normalize_days(request.query_params.get("days"), self.ALLOWED_DAYS, default=28)
        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)

        min_impressions = _parse_qp_int(request, "min_impressions")
        min_events = _parse_qp_int(request, "min_events")
        min_days_with_data = _parse_qp_int(request, "min_days_with_data")
        min_spend = _parse_qp_decimal(request, "min_spend")
        include_inactive = _parse_qp_bool(request, "include_inactive", default=False)
        ids = _parse_ids(request.query_params.get("ids", ""))

        campaign_id_raw = request.query_params.get("campaign_id")
        adset_id_raw = request.query_params.get("adset_id")
        campaign_id: int | None = None
        adset_id: int | None = None
        if campaign_id_raw:
            try:
                campaign_id = int(campaign_id_raw)
            except ValueError:
                return Response(
                    {"detail": "campaign_id must be an integer."}, status=400
                )
        if adset_id_raw:
            try:
                adset_id = int(adset_id_raw)
            except ValueError:
                return Response(
                    {"detail": "adset_id must be an integer."}, status=400
                )

        qs = _build_ad_perf_queryset(
            ad_account=ad_account,
            days=days,
            since=since,
            today=today,
            ids=ids,
            include_inactive=include_inactive,
            min_impressions=min_impressions,
            min_spend=min_spend,
            min_days_with_data=min_days_with_data,
            campaign_id=campaign_id,
            adset_id=adset_id,
        )

        rows = []
        for ad in qs:
            spend = ad.total_spend or Decimal("0")
            rev = ad.total_revenue or Decimal("0")
            imp = ad.total_impressions or 0
            clicks = ad.total_clicks or 0
            leads = ad.total_leads or 0
            calls = ad.total_calls or 0
            purchases = ad.total_purchases or 0
            messages = ad.total_messages or 0
            p25 = ad.total_video_p25 or 0
            p75 = ad.total_video_p75 or 0
            p100 = ad.total_video_p100 or 0
            v3 = ad.total_video_3sec or 0
            lpv = ad.total_lpv or 0
            comments = ad.total_comments or 0
            days_with_data = ad.n_days_with_data or 0
            total_events = leads + calls + purchases + messages
            if days < 7:
                is_in_learning = None
            else:
                is_in_learning = (total_events * 7) < (50 * days)
            creative = ad.creative
            rows.append({
                "id": ad.id,
                "meta_ad_id": ad.meta_ad_id,
                "name": ad.name,
                "effective_status": ad.effective_status,
                "adset_id": ad.adset_id,
                "adset_name": ad.adset.name if ad.adset else "",
                "campaign_id": ad.adset.campaign_id if ad.adset else None,
                "campaign_name": ad.adset.campaign.name if ad.adset and ad.adset.campaign else "",
                "creative": (
                    {
                        "id": creative.id,
                        "slug": creative.slug,
                        "meta_creative_id": creative.meta_creative_id,
                        "title": creative.title,
                        "thumbnail_url": creative.thumbnail_url,
                        "video_id": creative.video_id,
                        "object_type": creative.object_type,
                    }
                    if creative
                    else None
                ),
                "spend": str(spend),
                "impressions": imp,
                "clicks": clicks,
                "leads": leads,
                "calls": calls,
                "purchases": purchases,
                "messages": messages,
                "revenue": str(rev),
                "ctr": str(_safe_ratio(clicks, imp) * Decimal("100")),
                "cpc": str(_safe_ratio(spend, clicks)),
                "cpl": str(_safe_ratio(spend, leads)),
                "cpa": str(_safe_ratio(spend, purchases)),
                "roas": str(_safe_ratio(rev, spend)),
                "hook_rate": str(_safe_ratio(p25, imp) * Decimal("100")),
                "hook_rate_strict": str(_safe_ratio(v3, imp) * Decimal("100")),
                "hold_rate": str(_safe_ratio(p75, p25) * Decimal("100")),
                "completion_rate": str(_safe_ratio(p100, p25) * Decimal("100")),
                "video_3sec_count": v3,
                "lpv_count": lpv,
                "cost_per_lpv": str(_safe_ratio(spend, lpv)),
                "comment_count": comments,
                "cost_per_comment": str(_safe_ratio(spend, comments)),
                "total_events": total_events,
                "days_with_data": days_with_data,
                "is_in_learning": is_in_learning,
            })
        if ids:
            # Preserve input order strictly; ids that did not resolve are
            # absent from rows and naturally drop out of the response.
            order_index = {ad_id: i for i, ad_id in enumerate(ids)}
            rows.sort(key=lambda r: order_index[r["id"]])
        else:
            if min_events > 0:
                rows = [r for r in rows if r["total_events"] >= min_events]
            rows.sort(key=lambda r: Decimal(r["spend"]), reverse=True)
        return Response({
            "ad_account_id": ad_account.id,
            "currency": ad_account.currency,
            "days": days,
            "window": {"since": since.isoformat(), "until": today.isoformat()},
            "filters": {
                "min_impressions": min_impressions,
                "min_spend": str(min_spend),
                "min_events": min_events,
                "min_days_with_data": min_days_with_data,
                "include_inactive": include_inactive,
                "ids": ids,
            },
            "ads": rows,
        })


CSV_EXPORT_HEADER: tuple[str, ...] = (
    "Ad ID",
    "Ad Name",
    "Campaign",
    "Ad Set",
    "Creative ID",
    "Spend (account currency)",
    "Impressions",
    # Reach values are summed across the daily rows in the window; the same
    # person counted on more than one day is added each time, so this column
    # over-states true window-unique reach. Frequency in the next column
    # inherits the same caveat.
    "Reach",
    "Frequency",
    "Days with data",
    "ROAS",
    "CPA",
    "CVR",
    "Hook Rate (strict)",
    "Hold Rate",
    "CTR",
    "Completion Rate",
    "LPV Count",
    "Comment Count",
    "3-sec Views",
    "Total Events",
    "In Learning",
    "Creative Reuse Count",
)


class _CsvEcho:
    """File-like buffer that returns its writes instead of accumulating them.

    Pairing this with `csv.writer` and a generator yields a row at a time, so
    the response streams without building the full document in memory.
    """

    def write(self, value: str) -> str:
        return value


def _csv_decimal4(value) -> str:
    """Format a numeric value as a plain four-decimal string.

    Empty on null / non-numeric. Used for derived ratios and percentages so
    spreadsheet apps parse them as numbers (no currency symbol, no `%`).
    """
    if value is None:
        return ""
    try:
        d = Decimal(str(value))
    except Exception:
        return ""
    return f"{d:.4f}"


def _csv_money(value) -> str:
    """Render a Decimal money amount as a plain decimal string.

    Currency symbol is intentionally absent — the column header carries the
    currency context. Returns `"0"` for null values to keep the column numeric.
    """
    if value is None:
        return "0"
    return str(value)


def _csv_int(value) -> str:
    if value is None:
        return "0"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "0"


def _csv_yes_no_blank(value) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return ""


def _csv_row_for_ad(ad, days: int) -> tuple[str, ...]:
    """Build the 23-cell CSV row for one annotated MetaAd."""
    spend = ad.total_spend or Decimal("0")
    rev = ad.total_revenue or Decimal("0")
    imp = ad.total_impressions or 0
    reach = ad.total_reach or 0
    clicks = ad.total_clicks or 0
    leads = ad.total_leads or 0
    calls = ad.total_calls or 0
    purchases = ad.total_purchases or 0
    messages = ad.total_messages or 0
    p25 = ad.total_video_p25 or 0
    p75 = ad.total_video_p75 or 0
    p100 = ad.total_video_p100 or 0
    v3 = ad.total_video_3sec or 0
    lpv = ad.total_lpv or 0
    comments = ad.total_comments or 0
    days_with_data = ad.n_days_with_data or 0
    total_events = leads + calls + purchases + messages
    if days < 7:
        is_in_learning = None
    else:
        is_in_learning = (total_events * 7) < (50 * days)

    if reach:
        frequency_cell = _csv_decimal4(Decimal(str(imp)) / Decimal(str(reach)))
    else:
        frequency_cell = ""

    if clicks:
        cvr_cell = _csv_decimal4(Decimal(str(purchases)) / Decimal(str(clicks)))
    else:
        cvr_cell = ""

    creative_meta_id = ad.creative.meta_creative_id if ad.creative else ""
    creative_reuse = getattr(ad, "creative_ad_count", None) or 1

    campaign_name = (
        ad.adset.campaign.name if ad.adset and ad.adset.campaign else ""
    )
    adset_name = ad.adset.name if ad.adset else ""

    return (
        ad.meta_ad_id,
        ad.name,
        campaign_name,
        adset_name,
        creative_meta_id,
        _csv_money(spend),
        _csv_int(imp),
        _csv_int(reach),
        frequency_cell,
        _csv_int(days_with_data),
        _csv_decimal4(_safe_ratio(rev, spend)),
        _csv_decimal4(_safe_ratio(spend, purchases)),
        cvr_cell,
        _csv_decimal4(_safe_ratio(v3, imp) * Decimal("100")),
        _csv_decimal4(_safe_ratio(p75, p25) * Decimal("100")),
        _csv_decimal4(_safe_ratio(clicks, imp) * Decimal("100")),
        _csv_decimal4(_safe_ratio(p100, p25) * Decimal("100")),
        _csv_int(lpv),
        _csv_int(comments),
        _csv_int(v3),
        _csv_int(total_events),
        _csv_yes_no_blank(is_in_learning),
        _csv_int(creative_reuse),
    )


class MetaAdExportCsvView(APIView):
    """Stream a CSV of per-ad performance for an account-scoped query.

    Mirrors `MetaAdPerformanceView`'s filter shape so the same query string
    can be hit for either JSON or CSV. The response is a chunked
    `StreamingHttpResponse` because a full ad account can be ~1k ads and
    materialising the document in memory would push the worker hot under
    parallel exports.

    Reuses `_build_ad_perf_queryset` for the annotate + scoping + filter
    chain, then layers on a `creative_ad_count` annotate so the export can
    surface how often the same creative is rotated within the account.
    """

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def get(self, request, ad_account_id: int):
        ad_account = _user_ad_account(request, ad_account_id)
        days = _normalize_days(request.query_params.get("days"), self.ALLOWED_DAYS, default=28)
        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)

        min_impressions = _parse_qp_int(request, "min_impressions")
        min_events = _parse_qp_int(request, "min_events")
        min_days_with_data = _parse_qp_int(request, "min_days_with_data")
        min_spend = _parse_qp_decimal(request, "min_spend")
        include_inactive = _parse_qp_bool(request, "include_inactive", default=False)
        ids = _parse_ids(request.query_params.get("ids", ""))

        campaign_id_raw = request.query_params.get("campaign_id")
        adset_id_raw = request.query_params.get("adset_id")
        campaign_id: int | None = None
        adset_id: int | None = None
        if campaign_id_raw:
            try:
                campaign_id = int(campaign_id_raw)
            except ValueError:
                return Response(
                    {"detail": "campaign_id must be an integer."}, status=400
                )
        if adset_id_raw:
            try:
                adset_id = int(adset_id_raw)
            except ValueError:
                return Response(
                    {"detail": "adset_id must be an integer."}, status=400
                )

        qs = _build_ad_perf_queryset(
            ad_account=ad_account,
            days=days,
            since=since,
            today=today,
            ids=ids,
            include_inactive=include_inactive,
            min_impressions=min_impressions,
            min_spend=min_spend,
            min_days_with_data=min_days_with_data,
            campaign_id=campaign_id,
            adset_id=adset_id,
        )
        # Subquery rather than a sibling annotate: a `Count("creative__ads")`
        # would add a second join path on top of the insights_daily joins,
        # cartesian-multiplying the per-row SUMs and silently doubling spend
        # for any ad whose creative is shared by another ad in the account.
        creative_reuse_sq = (
            MetaAd.objects
            .filter(
                creative_id=OuterRef("creative_id"),
                adset__campaign__ad_account=ad_account,
            )
            .values("creative_id")
            .annotate(reuse=models.Count("id"))
            .values("reuse")
        )
        qs = qs.annotate(
            creative_ad_count=Subquery(
                creative_reuse_sq, output_field=IntegerField()
            ),
        )

        ads = list(qs)
        if not ids and min_events > 0:
            ads = [
                a
                for a in ads
                if (
                    (a.total_leads or 0)
                    + (a.total_calls or 0)
                    + (a.total_purchases or 0)
                    + (a.total_messages or 0)
                )
                >= min_events
            ]
        if ids:
            order_index = {ad_id: i for i, ad_id in enumerate(ids)}
            ads.sort(key=lambda a: order_index[a.id])
        else:
            ads.sort(key=lambda a: a.total_spend or Decimal("0"), reverse=True)

        pseudo = _CsvEcho()
        writer = csv.writer(pseudo)

        def stream():
            yield writer.writerow(CSV_EXPORT_HEADER)
            for ad in ads:
                yield writer.writerow(_csv_row_for_ad(ad, days))

        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        filename = f"meta-ads-{ad_account.id}-{days}d-{ts}.csv"
        response = StreamingHttpResponse(
            stream(), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


CREATIVE_CSV_EXPORT_HEADER: tuple[str, ...] = (
    "Creative ID",
    "Creative Name",
    "Title",
    "Body",
    "Object Type",
    "Call To Action",
    "Spend (account currency)",
    "Impressions",
    "Clicks",
    "Days with data",
    "ROAS",
    "CPA",
    "CVR",
    "Hook Rate (strict)",
    "Hold Rate",
    "CTR",
    "Completion Rate",
    "LPV Count",
    "Comment Count",
    "3-sec Views",
    "Total Events",
    "In Learning",
    "Ad Count",
)


CAMPAIGN_CSV_EXPORT_HEADER: tuple[str, ...] = (
    "Campaign ID",
    "Campaign Name",
    "Objective",
    "Status",
    "Spend (account currency)",
    "Impressions",
    "Clicks",
    "Leads",
    "Purchases",
    "Revenue",
    "ROAS",
    "CPA",
    "CVR",
    "CTR",
    "CPC",
    "CPM",
)


def _csv_row_for_creative(cr, days: int) -> tuple[str, ...]:
    """Build the CSV row tuple for one annotated MetaAdCreative."""
    spend = cr.total_spend or Decimal("0")
    rev = cr.total_revenue or Decimal("0")
    imp = cr.total_impressions or 0
    clicks = cr.total_clicks or 0
    leads = cr.total_leads or 0
    calls = cr.total_calls or 0
    purchases = cr.total_purchases or 0
    messages = cr.total_messages or 0
    p25 = cr.total_video_p25 or 0
    p75 = cr.total_video_p75 or 0
    p100 = cr.total_video_p100 or 0
    v3 = cr.total_video_3sec or 0
    lpv = cr.total_lpv or 0
    comments = cr.total_comments or 0
    days_with_data = cr.n_days_with_data or 0
    total_events = leads + calls + purchases + messages
    if days < 7:
        is_in_learning = None
    else:
        is_in_learning = (total_events * 7) < (50 * days)

    if clicks:
        cvr_cell = _csv_decimal4(
            Decimal(str(purchases)) / Decimal(str(clicks))
        )
    else:
        cvr_cell = ""

    return (
        cr.meta_creative_id,
        cr.name,
        cr.title,
        cr.body,
        cr.object_type,
        cr.call_to_action_type,
        _csv_money(spend),
        _csv_int(imp),
        _csv_int(clicks),
        _csv_int(days_with_data),
        _csv_decimal4(_safe_ratio(rev, spend)),
        _csv_decimal4(_safe_ratio(spend, purchases)),
        cvr_cell,
        _csv_decimal4(_safe_ratio(v3, imp) * Decimal("100")),
        _csv_decimal4(_safe_ratio(p75, p25) * Decimal("100")),
        _csv_decimal4(_safe_ratio(clicks, imp) * Decimal("100")),
        _csv_decimal4(_safe_ratio(p100, p25) * Decimal("100")),
        _csv_int(lpv),
        _csv_int(comments),
        _csv_int(v3),
        _csv_int(total_events),
        _csv_yes_no_blank(is_in_learning),
        _csv_int(cr.ad_count or 0),
    )


def _csv_row_for_campaign(camp) -> tuple[str, ...]:
    """Build the CSV row tuple for one annotated MetaCampaign."""
    spend = camp.total_spend or Decimal("0")
    rev = camp.total_revenue or Decimal("0")
    imp = camp.total_impressions or 0
    clicks = camp.total_clicks or 0
    leads = camp.total_leads or 0
    purchases = camp.total_purchases or 0

    if clicks:
        cvr_cell = _csv_decimal4(
            Decimal(str(purchases)) / Decimal(str(clicks))
        )
    else:
        cvr_cell = ""

    return (
        camp.meta_campaign_id,
        camp.name,
        camp.objective,
        camp.effective_status,
        _csv_money(spend),
        _csv_int(imp),
        _csv_int(clicks),
        _csv_int(leads),
        _csv_int(purchases),
        _csv_money(rev),
        _csv_decimal4(_safe_ratio(rev, spend)),
        _csv_decimal4(_safe_ratio(spend, purchases)),
        cvr_cell,
        _csv_decimal4(_safe_ratio(clicks, imp) * Decimal("100")),
        _csv_decimal4(_safe_ratio(spend, clicks)),
        _csv_decimal4(_safe_ratio(spend, imp) * Decimal("1000")),
    )


def _column_letter(index: int) -> str:
    """Return the Excel-style column name for a 0-based column index.

    0 -> 'A', 1 -> 'B', 25 -> 'Z', 26 -> 'AA', 27 -> 'AB', and so on.
    """
    if index < 0:
        raise ValueError("column index must be non-negative")
    letters = ""
    n = index + 1
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _classify_cell_value(value: str) -> tuple[str, str | None, Decimal | None]:
    """Classify a CSV cell string as either a number or a plain string.

    Empty strings stay empty. Anything that parses cleanly as a Decimal is
    stored as a number; everything else is stored as a string. Booleans
    and formulas are not auto-detected at this layer.
    """
    if value == "":
        return CellValueType.EMPTY, None, None
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return CellValueType.STRING, value, None
    return CellValueType.NUMBER, None, number


@transaction.atomic
def _populate_spreadsheet_with_grid(
    spreadsheet,
    sheet_name: str,
    headers: tuple[str, ...],
    rows: list[tuple[str, ...]],
):
    """Create a sheet under `spreadsheet` and populate it with `rows`.

    Row 0 carries the column headers. Subsequent rows carry the data.
    Columns are named with Excel letters (A, B, C, ...). Cells are
    classified as number when the string parses as a Decimal, otherwise
    as string. The whole population happens inside a transaction so a
    failure leaves no half-built sheet.
    """
    sheet = SheetService.create_sheet(spreadsheet, sheet_name)

    column_objects = SheetColumn.objects.bulk_create([
        SheetColumn(sheet=sheet, name=_column_letter(i), position=i)
        for i in range(len(headers))
    ])

    total_rows = 1 + len(rows)
    row_objects = SheetRow.objects.bulk_create([
        SheetRow(sheet=sheet, position=i) for i in range(total_rows)
    ])

    cells: list[Cell] = []
    # Header row.
    for col_index, header in enumerate(headers):
        cells.append(
            Cell(
                sheet=sheet,
                row=row_objects[0],
                column=column_objects[col_index],
                value_type=CellValueType.STRING,
                string_value=header,
                computed_type=ComputedCellType.STRING,
                computed_string=header,
                raw_input=header,
            )
        )
    # Data rows.
    for data_index, row_values in enumerate(rows):
        sheet_row = row_objects[data_index + 1]
        for col_index, value in enumerate(row_values):
            value_type, string_value, number_value = _classify_cell_value(value)
            if value_type == CellValueType.EMPTY:
                cells.append(
                    Cell(
                        sheet=sheet,
                        row=sheet_row,
                        column=column_objects[col_index],
                        value_type=CellValueType.EMPTY,
                        computed_type=ComputedCellType.EMPTY,
                        raw_input="",
                    )
                )
            elif value_type == CellValueType.NUMBER:
                cells.append(
                    Cell(
                        sheet=sheet,
                        row=sheet_row,
                        column=column_objects[col_index],
                        value_type=CellValueType.NUMBER,
                        number_value=number_value,
                        computed_type=ComputedCellType.NUMBER,
                        computed_number=number_value,
                        raw_input=value,
                    )
                )
            else:
                cells.append(
                    Cell(
                        sheet=sheet,
                        row=sheet_row,
                        column=column_objects[col_index],
                        value_type=CellValueType.STRING,
                        string_value=string_value,
                        computed_type=ComputedCellType.STRING,
                        computed_string=string_value,
                        raw_input=value,
                    )
                )
    Cell.objects.bulk_create(cells, batch_size=1000)
    return sheet


def _resolve_export_project(request) -> Project | None:
    """Read project_id from query params and return the Project, or None."""
    raw = request.query_params.get("project_id")
    if not raw:
        return None
    try:
        project_id = int(raw)
    except (TypeError, ValueError):
        return None
    project = Project.objects.filter(pk=project_id, is_deleted=False).first()
    if project is None:
        return None
    return project


class MetaCreativePerformanceCsvExportView(APIView):
    """Stream a CSV of per-creative performance, mirroring the ad-level export.

    Accepts the same filter shape as MetaCreativePerformanceView plus an
    optional `ids` query param so the frontend can export only the creatives
    the buyer has selected.
    """

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def get(self, request, ad_account_id: int):
        ad_account = _user_ad_account(request, ad_account_id)
        days = _normalize_days(
            request.query_params.get("days"), self.ALLOWED_DAYS, default=28
        )
        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)

        ids = _parse_ids(request.query_params.get("ids", ""))
        include_inactive = _parse_qp_bool(
            request, "include_inactive", default=False
        )
        include_shared_creatives = _parse_qp_bool(
            request, "include_shared_creatives", default=False
        )
        min_impressions = _parse_qp_int(request, "min_impressions")
        min_events = _parse_qp_int(request, "min_events")
        min_days_with_data = _parse_qp_int(request, "min_days_with_data")
        min_spend = _parse_qp_decimal(request, "min_spend")

        qs = _build_creative_perf_queryset(
            ad_account=ad_account,
            since=since,
            today=today,
            ids=ids,
            include_inactive=include_inactive,
            include_shared_creatives=include_shared_creatives,
            min_impressions=min_impressions,
            min_spend=min_spend,
            min_days_with_data=min_days_with_data,
        )

        creatives = list(qs)
        if not ids and min_events > 0:
            creatives = [
                c
                for c in creatives
                if (
                    (c.total_leads or 0)
                    + (c.total_calls or 0)
                    + (c.total_purchases or 0)
                    + (c.total_messages or 0)
                )
                >= min_events
            ]
        if ids:
            order_index = {pk: i for i, pk in enumerate(ids)}
            creatives.sort(key=lambda c: order_index[c.id])
        else:
            creatives.sort(
                key=lambda c: c.total_spend or Decimal("0"), reverse=True
            )

        pseudo = _CsvEcho()
        writer = csv.writer(pseudo)

        def stream():
            yield writer.writerow(CREATIVE_CSV_EXPORT_HEADER)
            for cr in creatives:
                yield writer.writerow(_csv_row_for_creative(cr, days))

        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        filename = f"meta-creatives-{ad_account.id}-{days}d-{ts}.csv"
        response = StreamingHttpResponse(
            stream(), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class MetaCampaignPerformanceCsvExportView(APIView):
    """Stream a CSV of per-campaign performance.

    Accepts an optional `ids` query param so the frontend can scope the
    export to selected campaigns.
    """

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def get(self, request, ad_account_id: int):
        ad_account = _user_ad_account(request, ad_account_id)
        days = _normalize_days(
            request.query_params.get("days"), self.ALLOWED_DAYS, default=28
        )
        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)
        ids = _parse_ids(request.query_params.get("ids", ""))

        qs = _build_campaign_perf_queryset(
            ad_account=ad_account, since=since, today=today, ids=ids
        )

        campaigns = list(qs)
        if ids:
            order_index = {pk: i for i, pk in enumerate(ids)}
            campaigns.sort(key=lambda c: order_index[c.id])
        else:
            campaigns.sort(
                key=lambda c: c.total_spend or Decimal("0"), reverse=True
            )

        pseudo = _CsvEcho()
        writer = csv.writer(pseudo)

        def stream():
            yield writer.writerow(CAMPAIGN_CSV_EXPORT_HEADER)
            for camp in campaigns:
                yield writer.writerow(_csv_row_for_campaign(camp))

        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        filename = f"meta-campaigns-{ad_account.id}-{days}d-{ts}.csv"
        response = StreamingHttpResponse(
            stream(), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


def _build_export_to_spreadsheet_response(
    *,
    request,
    project: Project,
    sheet_name: str,
    headers: tuple[str, ...],
    rows: list[tuple[str, ...]],
):
    """Common Spreadsheet-creation path for the three export endpoints.

    Validates and pulls the requested name out of the body, creates a
    Spreadsheet via SpreadsheetService.create_spreadsheet, then writes a
    populated sheet under it and returns the 201 envelope.
    """
    raw_name = request.data.get("name") if hasattr(request, "data") else None
    name = (raw_name or "").strip()
    if not name:
        return Response(
            {"detail": "name is required."}, status=400
        )
    try:
        spreadsheet = SpreadsheetService.create_spreadsheet(
            project=project, name=name
        )
    except ValidationError as err:
        return Response(
            {"detail": err.messages[0] if err.messages else str(err)},
            status=400,
        )
    _populate_spreadsheet_with_grid(spreadsheet, sheet_name, headers, rows)
    return Response(
        {
            "id": spreadsheet.id,
            "name": spreadsheet.name,
            "url": f"/spreadsheets/{spreadsheet.id}",
        },
        status=201,
    )


class MetaAdExportToSpreadsheetView(APIView):
    """Create a populated Spreadsheet from an ad-level export query."""

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def post(self, request, ad_account_id: int):
        ad_account = _user_ad_account(request, ad_account_id)
        project = _resolve_export_project(request)
        if project is None:
            return Response(
                {"detail": "project_id is required."}, status=400
            )
        days = _normalize_days(
            request.query_params.get("days"), self.ALLOWED_DAYS, default=28
        )
        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)

        ids = _parse_ids(request.query_params.get("ids", ""))
        include_inactive = _parse_qp_bool(
            request, "include_inactive", default=False
        )
        min_impressions = _parse_qp_int(request, "min_impressions")
        min_events = _parse_qp_int(request, "min_events")
        min_days_with_data = _parse_qp_int(request, "min_days_with_data")
        min_spend = _parse_qp_decimal(request, "min_spend")

        qs = _build_ad_perf_queryset(
            ad_account=ad_account,
            days=days,
            since=since,
            today=today,
            ids=ids,
            include_inactive=include_inactive,
            min_impressions=min_impressions,
            min_spend=min_spend,
            min_days_with_data=min_days_with_data,
        )
        qs = qs.annotate(
            creative_ad_count=Subquery(
                MetaAd.objects.filter(
                    creative_id=OuterRef("creative_id"),
                    adset__campaign__ad_account=ad_account,
                )
                .values("creative_id")
                .annotate(reuse=models.Count("id"))
                .values("reuse"),
                output_field=IntegerField(),
            )
        )

        ads = list(qs)
        if not ids and min_events > 0:
            ads = [
                a
                for a in ads
                if (
                    (a.total_leads or 0)
                    + (a.total_calls or 0)
                    + (a.total_purchases or 0)
                    + (a.total_messages or 0)
                )
                >= min_events
            ]
        if ids:
            order_index = {pk: i for i, pk in enumerate(ids)}
            ads.sort(key=lambda a: order_index[a.id])
        else:
            ads.sort(key=lambda a: a.total_spend or Decimal("0"), reverse=True)

        rows = [_csv_row_for_ad(ad, days) for ad in ads]
        return _build_export_to_spreadsheet_response(
            request=request,
            project=project,
            sheet_name="Ads",
            headers=CSV_EXPORT_HEADER,
            rows=rows,
        )


class MetaCreativeExportToSpreadsheetView(APIView):
    """Create a populated Spreadsheet from a creative-level export query."""

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def post(self, request, ad_account_id: int):
        ad_account = _user_ad_account(request, ad_account_id)
        project = _resolve_export_project(request)
        if project is None:
            return Response(
                {"detail": "project_id is required."}, status=400
            )
        days = _normalize_days(
            request.query_params.get("days"), self.ALLOWED_DAYS, default=28
        )
        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)

        ids = _parse_ids(request.query_params.get("ids", ""))
        include_inactive = _parse_qp_bool(
            request, "include_inactive", default=False
        )
        include_shared_creatives = _parse_qp_bool(
            request, "include_shared_creatives", default=False
        )
        min_impressions = _parse_qp_int(request, "min_impressions")
        min_events = _parse_qp_int(request, "min_events")
        min_days_with_data = _parse_qp_int(request, "min_days_with_data")
        min_spend = _parse_qp_decimal(request, "min_spend")

        qs = _build_creative_perf_queryset(
            ad_account=ad_account,
            since=since,
            today=today,
            ids=ids,
            include_inactive=include_inactive,
            include_shared_creatives=include_shared_creatives,
            min_impressions=min_impressions,
            min_spend=min_spend,
            min_days_with_data=min_days_with_data,
        )

        creatives = list(qs)
        if not ids and min_events > 0:
            creatives = [
                c
                for c in creatives
                if (
                    (c.total_leads or 0)
                    + (c.total_calls or 0)
                    + (c.total_purchases or 0)
                    + (c.total_messages or 0)
                )
                >= min_events
            ]
        if ids:
            order_index = {pk: i for i, pk in enumerate(ids)}
            creatives.sort(key=lambda c: order_index[c.id])
        else:
            creatives.sort(
                key=lambda c: c.total_spend or Decimal("0"), reverse=True
            )

        rows = [_csv_row_for_creative(cr, days) for cr in creatives]
        return _build_export_to_spreadsheet_response(
            request=request,
            project=project,
            sheet_name="Creatives",
            headers=CREATIVE_CSV_EXPORT_HEADER,
            rows=rows,
        )


class MetaCampaignExportToSpreadsheetView(APIView):
    """Create a populated Spreadsheet from a campaign-level export query."""

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def post(self, request, ad_account_id: int):
        ad_account = _user_ad_account(request, ad_account_id)
        project = _resolve_export_project(request)
        if project is None:
            return Response(
                {"detail": "project_id is required."}, status=400
            )
        days = _normalize_days(
            request.query_params.get("days"), self.ALLOWED_DAYS, default=28
        )
        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)
        ids = _parse_ids(request.query_params.get("ids", ""))

        qs = _build_campaign_perf_queryset(
            ad_account=ad_account, since=since, today=today, ids=ids
        )

        campaigns = list(qs)
        if ids:
            order_index = {pk: i for i, pk in enumerate(ids)}
            campaigns.sort(key=lambda c: order_index[c.id])
        else:
            campaigns.sort(
                key=lambda c: c.total_spend or Decimal("0"), reverse=True
            )

        rows = [_csv_row_for_campaign(camp) for camp in campaigns]
        return _build_export_to_spreadsheet_response(
            request=request,
            project=project,
            sheet_name="Campaigns",
            headers=CAMPAIGN_CSV_EXPORT_HEADER,
            rows=rows,
        )


class MetaCreativeDetailView(APIView):
    """Full metadata for a single creative plus the ads that reference it.

    Used by the creative detail page. Aggregates rolling-window performance
    at the creative level (summed across every linked ad) so the page can
    render KPIs without an extra round-trip.
    """

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def get(self, request, creative_id: int):
        creative = get_object_or_404(
            MetaAdCreative,
            pk=resolve_pk_for(MetaAdCreative, creative_id),
            ad_account_id__in=_accessible_ad_accounts_for_user(request.user),
        )
        days = _normalize_days(request.query_params.get("days"), self.ALLOWED_DAYS, default=28)
        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)

        linked_ads = (
            MetaAd.objects.filter(creative=creative)
            .select_related("adset", "adset__campaign")
            .annotate(
                total_spend=Sum(
                    "insights_daily__spend",
                    filter=models.Q(
                        insights_daily__date__gte=since,
                        insights_daily__date__lte=today,
                    ),
                ),
                total_impressions=Sum(
                    "insights_daily__impressions",
                    filter=models.Q(
                        insights_daily__date__gte=since,
                        insights_daily__date__lte=today,
                    ),
                ),
                total_clicks=Sum(
                    "insights_daily__clicks",
                    filter=models.Q(
                        insights_daily__date__gte=since,
                        insights_daily__date__lte=today,
                    ),
                ),
                total_leads=Sum(
                    "insights_daily__leads",
                    filter=models.Q(
                        insights_daily__date__gte=since,
                        insights_daily__date__lte=today,
                    ),
                ),
                total_purchases=Sum(
                    "insights_daily__purchases",
                    filter=models.Q(
                        insights_daily__date__gte=since,
                        insights_daily__date__lte=today,
                    ),
                ),
                total_revenue=Sum(
                    "insights_daily__revenue",
                    filter=models.Q(
                        insights_daily__date__gte=since,
                        insights_daily__date__lte=today,
                    ),
                ),
            )
        )

        agg = MetaInsightDaily.objects.filter(
            ad__creative=creative,
            date__gte=since,
            date__lte=today,
        ).aggregate(
            spend=Sum("spend"),
            impressions=Sum("impressions"),
            clicks=Sum("clicks"),
            reach=Sum("reach"),
            leads=Sum("leads"),
            calls=Sum("calls"),
            purchases=Sum("purchases"),
            revenue=Sum("revenue"),
            video_p25=Sum("video_p25"),
            video_p50=Sum("video_p50"),
            video_p75=Sum("video_p75"),
            video_p100=Sum("video_p100"),
        )
        agg = {k: (v if v is not None else 0) for k, v in agg.items()}
        spend = agg["spend"] or Decimal("0")
        revenue = agg["revenue"] or Decimal("0")
        impressions = agg["impressions"] or 0
        p25 = agg["video_p25"] or 0
        p50 = agg["video_p50"] or 0
        p75 = agg["video_p75"] or 0
        p100 = agg["video_p100"] or 0
        aggregates = {
            "spend": str(spend),
            "impressions": impressions,
            "clicks": agg["clicks"] or 0,
            "reach": agg["reach"] or 0,
            "leads": agg["leads"] or 0,
            "calls": agg["calls"] or 0,
            "purchases": agg["purchases"] or 0,
            "revenue": str(revenue),
            "ctr": str(_safe_ratio(agg["clicks"], impressions) * Decimal("100")),
            "cpc": str(_safe_ratio(spend, agg["clicks"])),
            "cpm": str(_safe_ratio(spend, impressions) * Decimal("1000")),
            "cpl": str(_safe_ratio(spend, agg["leads"])),
            "cpa": str(_safe_ratio(spend, agg["purchases"])),
            "roas": str(_safe_ratio(revenue, spend)),
            "video_p25": p25,
            "video_p50": p50,
            "video_p75": p75,
            "video_p100": p100,
            "hook_rate": str(_safe_ratio(p25, impressions) * Decimal("100")),
            "hold_rate": str(_safe_ratio(p75, p25) * Decimal("100")),
            "completion_rate": str(_safe_ratio(p100, p25) * Decimal("100")),
        }

        linked_ads_payload = []
        for ad in linked_ads:
            ad_spend = ad.total_spend or Decimal("0")
            ad_rev = ad.total_revenue or Decimal("0")
            linked_ads_payload.append({
                "id": ad.id,
                "meta_ad_id": ad.meta_ad_id,
                "name": ad.name,
                "effective_status": ad.effective_status,
                "campaign_name": ad.adset.campaign.name if ad.adset and ad.adset.campaign else "",
                "adset_name": ad.adset.name if ad.adset else "",
                "spend": str(ad_spend),
                "impressions": ad.total_impressions or 0,
                "clicks": ad.total_clicks or 0,
                "leads": ad.total_leads or 0,
                "purchases": ad.total_purchases or 0,
                "revenue": str(ad_rev),
                "roas": str(_safe_ratio(ad_rev, ad_spend)),
            })
        linked_ads_payload.sort(key=lambda r: Decimal(r["spend"]), reverse=True)

        return Response({
            "id": creative.id,
            "slug": creative.slug,
            "meta_creative_id": creative.meta_creative_id,
            "ad_account_id": creative.ad_account_id,
            "currency": creative.ad_account.currency,
            "name": creative.name,
            "title": creative.title,
            "body": creative.body,
            "image_url": creative.image_url,
            "thumbnail_url": creative.thumbnail_url,
            "video_id": creative.video_id,
            "object_type": creative.object_type,
            "call_to_action_type": creative.call_to_action_type,
            "asset_feed_spec": creative.asset_feed_spec or {},
            "created_at": creative.created_at.isoformat(),
            "updated_at": creative.updated_at.isoformat(),
            "days": days,
            "window": {"since": since.isoformat(), "until": today.isoformat()},
            "aggregates": aggregates,
            "linked_ads": linked_ads_payload,
            "linked_ads_count": len(linked_ads_payload),
        })


class MetaCreativeVideoSourceView(APIView):
    """Return a Meta-rendered ad preview iframe URL for the creative.

    Directly fetching `/{video_id}?fields=source` requires `ads_management`
    scope which our app does not have. The Ad Previews API works with the
    `ads_read` scope we do have, and returns an iframe that Meta renders
    (video autoplay + body + CTA) — functionally the same as seeing the ad
    in Ads Manager's preview panel.

    We need *an ad* to ask for a preview of, so we pick the first ad that
    references this creative. Creatives with no linked ad (a known FK gap
    in the sync, G-01) return 400 with a specific detail message.
    """

    permission_classes = [IsAuthenticated]
    VALID_FORMATS = {
        "MOBILE_FEED_STANDARD",
        "DESKTOP_FEED_STANDARD",
        "FACEBOOK_STORY_MOBILE",
        "INSTAGRAM_STANDARD",
        "INSTAGRAM_STORY",
        "FACEBOOK_REELS_MOBILE",
    }

    def get(self, request, creative_id: int):
        creative = get_object_or_404(
            MetaAdCreative.objects.select_related("ad_account__connection"),
            pk=resolve_pk_for(MetaAdCreative, creative_id),
            ad_account_id__in=_accessible_ad_accounts_for_user(request.user),
        )

        requested_format = request.query_params.get("ad_format", "MOBILE_FEED_STANDARD")
        if requested_format not in self.VALID_FORMATS:
            requested_format = "MOBILE_FEED_STANDARD"

        try:
            payload = get_creative_preview(creative, requested_format)
        except CreativePreviewError as err:
            body = {"detail": err.detail, **err.extra}
            if err.code:
                body["code"] = err.code
            return Response(body, status=err.status)

        return Response(payload)


class MetaCreativeInsightTimeseriesView(APIView):
    """Daily aggregates for a creative — summed across every linked ad."""

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def get(self, request, creative_id: int):
        creative = get_object_or_404(
            MetaAdCreative,
            pk=resolve_pk_for(MetaAdCreative, creative_id),
            ad_account_id__in=_accessible_ad_accounts_for_user(request.user),
        )
        days = _normalize_days(request.query_params.get("days"), self.ALLOWED_DAYS, default=28)
        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)

        qs = MetaInsightDaily.objects.filter(
            ad__creative=creative,
            date__gte=since,
            date__lte=today,
        )
        daily = qs.values("date").annotate(
            spend=Sum("spend"),
            impressions=Sum("impressions"),
            clicks=Sum("clicks"),
            leads=Sum("leads"),
            purchases=Sum("purchases"),
            revenue=Sum("revenue"),
            video_p25=Sum("video_p25"),
            video_p75=Sum("video_p75"),
            video_p100=Sum("video_p100"),
        )
        by_date = {row["date"]: row for row in daily}

        points = []
        cur = since
        while cur <= today:
            r = by_date.get(cur)
            imp = (r["impressions"] if r else 0) or 0
            p25 = (r["video_p25"] if r else 0) or 0
            p75 = (r["video_p75"] if r else 0) or 0
            points.append({
                "date": cur.isoformat(),
                "spend": str((r["spend"] if r else Decimal("0")) or Decimal("0")),
                "impressions": imp,
                "clicks": (r["clicks"] if r else 0) or 0,
                "leads": (r["leads"] if r else 0) or 0,
                "purchases": (r["purchases"] if r else 0) or 0,
                "revenue": str((r["revenue"] if r else Decimal("0")) or Decimal("0")),
                "video_p25": p25,
                "video_p75": p75,
                "video_p100": (r["video_p100"] if r else 0) or 0,
                "hook_rate": str(_safe_ratio(p25, imp) * Decimal("100")),
                "hold_rate": str(_safe_ratio(p75, p25) * Decimal("100")),
            })
            cur += _dt.timedelta(days=1)

        return Response({
            "creative_id": creative.id,
            "meta_creative_id": creative.meta_creative_id,
            "currency": creative.ad_account.currency,
            "days": days,
            "window": {"since": since.isoformat(), "until": today.isoformat()},
            "points": points,
        })


class MetaCampaignDetailView(APIView):
    """Full metadata for a single campaign plus its adsets, with window aggregates.

    Used by the campaign detail page. Aggregates rolling-window performance
    at the campaign level (summed across every descendant ad) so the page
    can render KPIs without an extra round-trip.
    """

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def get(self, request, campaign_id: int):
        campaign = get_object_or_404(
            MetaCampaign,
            pk=resolve_pk_for(MetaCampaign, campaign_id),
            ad_account_id__in=_accessible_ad_accounts_for_user(request.user),
        )
        days = _normalize_days(request.query_params.get("days"), self.ALLOWED_DAYS, default=28)
        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)

        agg = MetaInsightDaily.objects.filter(
            ad__adset__campaign=campaign,
            date__gte=since,
            date__lte=today,
        ).aggregate(
            spend=Sum("spend"),
            impressions=Sum("impressions"),
            clicks=Sum("clicks"),
            reach=Sum("reach"),
            leads=Sum("leads"),
            calls=Sum("calls"),
            purchases=Sum("purchases"),
            revenue=Sum("revenue"),
        )
        agg = {k: (v if v is not None else 0) for k, v in agg.items()}
        spend = agg["spend"] or Decimal("0")
        revenue = agg["revenue"] or Decimal("0")
        impressions = agg["impressions"] or 0
        aggregates = {
            "spend": str(spend),
            "impressions": impressions,
            "clicks": agg["clicks"] or 0,
            "reach": agg["reach"] or 0,
            "leads": agg["leads"] or 0,
            "calls": agg["calls"] or 0,
            "purchases": agg["purchases"] or 0,
            "revenue": str(revenue),
            "ctr": str(_safe_ratio(agg["clicks"], impressions) * Decimal("100")),
            "cpc": str(_safe_ratio(spend, agg["clicks"])),
            "cpm": str(_safe_ratio(spend, impressions) * Decimal("1000")),
            "cpl": str(_safe_ratio(spend, agg["leads"])),
            "cpa": str(_safe_ratio(spend, agg["purchases"])),
            "roas": str(_safe_ratio(revenue, spend)),
        }

        linked_adsets = MetaAdSet.objects.filter(campaign=campaign).annotate(
            total_spend=Sum(
                "ads__insights_daily__spend",
                filter=models.Q(
                    ads__insights_daily__date__gte=since,
                    ads__insights_daily__date__lte=today,
                ),
            ),
            total_impressions=Sum(
                "ads__insights_daily__impressions",
                filter=models.Q(
                    ads__insights_daily__date__gte=since,
                    ads__insights_daily__date__lte=today,
                ),
            ),
            total_clicks=Sum(
                "ads__insights_daily__clicks",
                filter=models.Q(
                    ads__insights_daily__date__gte=since,
                    ads__insights_daily__date__lte=today,
                ),
            ),
            total_leads=Sum(
                "ads__insights_daily__leads",
                filter=models.Q(
                    ads__insights_daily__date__gte=since,
                    ads__insights_daily__date__lte=today,
                ),
            ),
            total_purchases=Sum(
                "ads__insights_daily__purchases",
                filter=models.Q(
                    ads__insights_daily__date__gte=since,
                    ads__insights_daily__date__lte=today,
                ),
            ),
            total_revenue=Sum(
                "ads__insights_daily__revenue",
                filter=models.Q(
                    ads__insights_daily__date__gte=since,
                    ads__insights_daily__date__lte=today,
                ),
            ),
        )
        adset_rows = []
        for a in linked_adsets:
            a_spend = a.total_spend or Decimal("0")
            a_rev = a.total_revenue or Decimal("0")
            adset_rows.append({
                "id": a.id,
                "slug": a.slug,
                "meta_adset_id": a.meta_adset_id,
                "name": a.name,
                "effective_status": a.effective_status,
                "optimization_goal": a.optimization_goal,
                "daily_budget_cents": a.daily_budget_cents,
                "lifetime_budget_cents": a.lifetime_budget_cents,
                "spend": str(a_spend),
                "impressions": a.total_impressions or 0,
                "clicks": a.total_clicks or 0,
                "leads": a.total_leads or 0,
                "purchases": a.total_purchases or 0,
                "revenue": str(a_rev),
                "roas": str(_safe_ratio(a_rev, a_spend)),
            })
        adset_rows.sort(key=lambda r: Decimal(r["spend"]), reverse=True)

        return Response({
            "id": campaign.id,
            "slug": campaign.slug,
            "meta_campaign_id": campaign.meta_campaign_id,
            "ad_account_id": campaign.ad_account_id,
            "currency": campaign.ad_account.currency,
            "name": campaign.name,
            "objective": campaign.objective,
            "status": campaign.status,
            "effective_status": campaign.effective_status,
            "start_time": campaign.start_time.isoformat() if campaign.start_time else None,
            "stop_time": campaign.stop_time.isoformat() if campaign.stop_time else None,
            "daily_budget_cents": campaign.daily_budget_cents,
            "lifetime_budget_cents": campaign.lifetime_budget_cents,
            "special_ad_categories": campaign.special_ad_categories or [],
            "created_at": campaign.created_at.isoformat(),
            "updated_at": campaign.updated_at.isoformat(),
            "days": days,
            "window": {"since": since.isoformat(), "until": today.isoformat()},
            "aggregates": aggregates,
            "linked_adsets": adset_rows,
            "linked_adsets_count": len(adset_rows),
        })


class MetaCampaignInsightTimeseriesView(APIView):
    """Daily aggregates for a campaign — summed across every descendant ad."""

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def get(self, request, campaign_id: int):
        campaign = get_object_or_404(
            MetaCampaign,
            pk=resolve_pk_for(MetaCampaign, campaign_id),
            ad_account_id__in=_accessible_ad_accounts_for_user(request.user),
        )
        days = _normalize_days(request.query_params.get("days"), self.ALLOWED_DAYS, default=28)
        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)

        daily = MetaInsightDaily.objects.filter(
            ad__adset__campaign=campaign,
            date__gte=since,
            date__lte=today,
        ).values("date").annotate(
            spend=Sum("spend"),
            impressions=Sum("impressions"),
            clicks=Sum("clicks"),
            leads=Sum("leads"),
            purchases=Sum("purchases"),
            revenue=Sum("revenue"),
        )
        by_date = {row["date"]: row for row in daily}

        points = []
        cur = since
        while cur <= today:
            r = by_date.get(cur)
            points.append({
                "date": cur.isoformat(),
                "spend": str((r["spend"] if r else Decimal("0")) or Decimal("0")),
                "impressions": (r["impressions"] if r else 0) or 0,
                "clicks": (r["clicks"] if r else 0) or 0,
                "leads": (r["leads"] if r else 0) or 0,
                "purchases": (r["purchases"] if r else 0) or 0,
                "revenue": str((r["revenue"] if r else Decimal("0")) or Decimal("0")),
            })
            cur += _dt.timedelta(days=1)

        return Response({
            "campaign_id": campaign.id,
            "meta_campaign_id": campaign.meta_campaign_id,
            "currency": campaign.ad_account.currency,
            "days": days,
            "window": {"since": since.isoformat(), "until": today.isoformat()},
            "points": points,
        })


class MetaAdSetDetailView(APIView):
    """Full metadata for a single adset plus its ads, with window aggregates."""

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def get(self, request, adset_id: int):
        adset = get_object_or_404(
            MetaAdSet.objects.select_related("campaign", "campaign__ad_account"),
            pk=resolve_pk_for(MetaAdSet, adset_id),
            campaign__ad_account_id__in=_accessible_ad_accounts_for_user(request.user),
        )
        days = _normalize_days(request.query_params.get("days"), self.ALLOWED_DAYS, default=28)
        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)

        agg = MetaInsightDaily.objects.filter(
            ad__adset=adset,
            date__gte=since,
            date__lte=today,
        ).aggregate(
            spend=Sum("spend"),
            impressions=Sum("impressions"),
            clicks=Sum("clicks"),
            reach=Sum("reach"),
            leads=Sum("leads"),
            calls=Sum("calls"),
            purchases=Sum("purchases"),
            revenue=Sum("revenue"),
            video_p25=Sum("video_p25"),
            video_p75=Sum("video_p75"),
            video_p100=Sum("video_p100"),
        )
        agg = {k: (v if v is not None else 0) for k, v in agg.items()}
        spend = agg["spend"] or Decimal("0")
        revenue = agg["revenue"] or Decimal("0")
        impressions = agg["impressions"] or 0
        p25 = agg["video_p25"] or 0
        p75 = agg["video_p75"] or 0
        aggregates = {
            "spend": str(spend),
            "impressions": impressions,
            "clicks": agg["clicks"] or 0,
            "reach": agg["reach"] or 0,
            "leads": agg["leads"] or 0,
            "calls": agg["calls"] or 0,
            "purchases": agg["purchases"] or 0,
            "revenue": str(revenue),
            "ctr": str(_safe_ratio(agg["clicks"], impressions) * Decimal("100")),
            "cpc": str(_safe_ratio(spend, agg["clicks"])),
            "cpm": str(_safe_ratio(spend, impressions) * Decimal("1000")),
            "cpl": str(_safe_ratio(spend, agg["leads"])),
            "cpa": str(_safe_ratio(spend, agg["purchases"])),
            "roas": str(_safe_ratio(revenue, spend)),
            "hook_rate": str(_safe_ratio(p25, impressions) * Decimal("100")),
            "hold_rate": str(_safe_ratio(p75, p25) * Decimal("100")),
        }

        linked_ads = MetaAd.objects.filter(adset=adset).select_related("creative").annotate(
            total_spend=Sum(
                "insights_daily__spend",
                filter=models.Q(
                    insights_daily__date__gte=since,
                    insights_daily__date__lte=today,
                ),
            ),
            total_impressions=Sum(
                "insights_daily__impressions",
                filter=models.Q(
                    insights_daily__date__gte=since,
                    insights_daily__date__lte=today,
                ),
            ),
            total_clicks=Sum(
                "insights_daily__clicks",
                filter=models.Q(
                    insights_daily__date__gte=since,
                    insights_daily__date__lte=today,
                ),
            ),
            total_leads=Sum(
                "insights_daily__leads",
                filter=models.Q(
                    insights_daily__date__gte=since,
                    insights_daily__date__lte=today,
                ),
            ),
            total_purchases=Sum(
                "insights_daily__purchases",
                filter=models.Q(
                    insights_daily__date__gte=since,
                    insights_daily__date__lte=today,
                ),
            ),
            total_revenue=Sum(
                "insights_daily__revenue",
                filter=models.Q(
                    insights_daily__date__gte=since,
                    insights_daily__date__lte=today,
                ),
            ),
        )
        ad_rows = []
        for ad in linked_ads:
            a_spend = ad.total_spend or Decimal("0")
            a_rev = ad.total_revenue or Decimal("0")
            ad_rows.append({
                "id": ad.id,
                "meta_ad_id": ad.meta_ad_id,
                "name": ad.name,
                "effective_status": ad.effective_status,
                "creative": (
                    {
                        "id": ad.creative.id,
                        "slug": ad.creative.slug,
                        "meta_creative_id": ad.creative.meta_creative_id,
                        "title": ad.creative.title,
                        "thumbnail_url": ad.creative.thumbnail_url,
                    }
                    if ad.creative
                    else None
                ),
                "spend": str(a_spend),
                "impressions": ad.total_impressions or 0,
                "clicks": ad.total_clicks or 0,
                "leads": ad.total_leads or 0,
                "purchases": ad.total_purchases or 0,
                "revenue": str(a_rev),
                "roas": str(_safe_ratio(a_rev, a_spend)),
            })
        ad_rows.sort(key=lambda r: Decimal(r["spend"]), reverse=True)

        return Response({
            "id": adset.id,
            "meta_adset_id": adset.meta_adset_id,
            "ad_account_id": adset.campaign.ad_account_id,
            "currency": adset.campaign.ad_account.currency,
            "name": adset.name,
            "status": adset.status,
            "effective_status": adset.effective_status,
            "optimization_goal": adset.optimization_goal,
            "billing_event": adset.billing_event,
            "bid_amount_cents": adset.bid_amount_cents,
            "daily_budget_cents": adset.daily_budget_cents,
            "lifetime_budget_cents": adset.lifetime_budget_cents,
            "campaign": {
                "id": adset.campaign_id,
                "slug": adset.campaign.slug,
                "meta_campaign_id": adset.campaign.meta_campaign_id,
                "name": adset.campaign.name,
            },
            "created_at": adset.created_at.isoformat(),
            "updated_at": adset.updated_at.isoformat(),
            "days": days,
            "window": {"since": since.isoformat(), "until": today.isoformat()},
            "aggregates": aggregates,
            "linked_ads": ad_rows,
            "linked_ads_count": len(ad_rows),
        })


class MetaAdSetInsightTimeseriesView(APIView):
    """Daily aggregates for an adset — summed across every descendant ad."""

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def get(self, request, adset_id: int):
        adset = get_object_or_404(
            MetaAdSet,
            pk=resolve_pk_for(MetaAdSet, adset_id),
            campaign__ad_account_id__in=_accessible_ad_accounts_for_user(request.user),
        )
        days = _normalize_days(request.query_params.get("days"), self.ALLOWED_DAYS, default=28)
        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)

        daily = MetaInsightDaily.objects.filter(
            ad__adset=adset,
            date__gte=since,
            date__lte=today,
        ).values("date").annotate(
            spend=Sum("spend"),
            impressions=Sum("impressions"),
            clicks=Sum("clicks"),
            leads=Sum("leads"),
            purchases=Sum("purchases"),
            revenue=Sum("revenue"),
            video_p25=Sum("video_p25"),
            video_p75=Sum("video_p75"),
            video_p100=Sum("video_p100"),
        )
        by_date = {row["date"]: row for row in daily}

        points = []
        cur = since
        while cur <= today:
            r = by_date.get(cur)
            imp = (r["impressions"] if r else 0) or 0
            p25 = (r["video_p25"] if r else 0) or 0
            p75 = (r["video_p75"] if r else 0) or 0
            points.append({
                "date": cur.isoformat(),
                "spend": str((r["spend"] if r else Decimal("0")) or Decimal("0")),
                "impressions": imp,
                "clicks": (r["clicks"] if r else 0) or 0,
                "leads": (r["leads"] if r else 0) or 0,
                "purchases": (r["purchases"] if r else 0) or 0,
                "revenue": str((r["revenue"] if r else Decimal("0")) or Decimal("0")),
                "video_p25": p25,
                "video_p75": p75,
                "video_p100": (r["video_p100"] if r else 0) or 0,
                "hook_rate": str(_safe_ratio(p25, imp) * Decimal("100")),
                "hold_rate": str(_safe_ratio(p75, p25) * Decimal("100")),
            })
            cur += _dt.timedelta(days=1)

        return Response({
            "adset_id": adset.id,
            "meta_adset_id": adset.meta_adset_id,
            "currency": adset.campaign.ad_account.currency,
            "days": days,
            "window": {"since": since.isoformat(), "until": today.isoformat()},
            "points": points,
        })


class MetaAdInsightTimeseriesView(APIView):
    """Daily insights for a single ad — the "stock chart" data source.

    Returns a dense array of points across the requested window so the
    frontend can draw a brush-enabled time series without gap handling
    on the client side (missing days are emitted as zeroes).
    """

    permission_classes = [IsAuthenticated]
    ALLOWED_DAYS = {1, 2, 3, 7, 14, 28, 30}

    def get(self, request, ad_account_id: int, ad_id: int):
        ad_account = _user_ad_account(request, ad_account_id)
        ad = get_object_or_404(
            MetaAd,
            pk=ad_id,
            adset__campaign__ad_account=ad_account,
        )
        days = _normalize_days(request.query_params.get("days"), self.ALLOWED_DAYS, default=28)
        today = _dt.date.today()
        since = today - _dt.timedelta(days=days - 1)
        rows = MetaInsightDaily.objects.filter(
            ad=ad, date__gte=since, date__lte=today
        ).order_by("date")
        row_map = {r.date: r for r in rows}

        points = []
        cur = since
        while cur <= today:
            r = row_map.get(cur)
            imp = (r.impressions if r else 0) or 0
            p25 = (r.video_p25 if r else 0) or 0
            p75 = (r.video_p75 if r else 0) or 0
            points.append({
                "date": cur.isoformat(),
                "spend": str(r.spend) if r else "0",
                "impressions": imp,
                "clicks": (r.clicks if r else 0) or 0,
                "leads": (r.leads if r else 0) or 0,
                "purchases": (r.purchases if r else 0) or 0,
                "revenue": str(r.revenue) if r else "0",
                "video_p25": p25,
                "video_p75": p75,
                "video_p100": (r.video_p100 if r else 0) or 0,
                "hook_rate": str(_safe_ratio(p25, imp) * Decimal("100")),
                "hold_rate": str(_safe_ratio(p75, p25) * Decimal("100")),
            })
            cur += _dt.timedelta(days=1)

        return Response({
            "ad_account_id": ad_account.id,
            "ad_id": ad.id,
            "meta_ad_id": ad.meta_ad_id,
            "ad_name": ad.name,
            "currency": ad_account.currency,
            "days": days,
            "window": {"since": since.isoformat(), "until": today.isoformat()},
            "points": points,
        })


# Utils -----------------------------------------------------------------------


def _parse_date(value):
    if not value:
        return None
    try:
        return _dt.date.fromisoformat(value)
    except ValueError:
        return None


def _safe_ratio(numerator, denominator) -> Decimal:
    try:
        n = Decimal(str(numerator or 0))
        d = Decimal(str(denominator or 0))
        if d == 0:
            return Decimal("0")
        return n / d
    except Exception:
        return Decimal("0")


def _normalize_days(value, allowed: set[int], default: int) -> int:
    try:
        days = int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return days if days in allowed else default


def _parse_qp_int(request, name: str, default: int = 0) -> int:
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return default


def _parse_qp_decimal(request, name: str, default: Decimal = Decimal("0")) -> Decimal:
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = Decimal(str(raw))
    except Exception:
        return default
    return value if value >= 0 else default


def _parse_qp_bool(request, name: str, default: bool = False) -> bool:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_ids(raw: str) -> list[int]:
    """Parse a comma-separated id list from a URL query param.

    Returns a deduped list of positive integers preserving first-occurrence
    order. Malformed (non-integer, zero, negative) entries are silently
    dropped — a single typo should not 400 the whole request. The list is
    truncated to 20 entries server-side as a defensive bound; callers may
    impose their own lower UX caps.
    """
    if not raw:
        return []
    out: list[int] = []
    seen: set[int] = set()
    for piece in raw.split(","):
        s = piece.strip()
        if not s:
            continue
        try:
            n = int(s)
        except ValueError:
            continue
        if n <= 0 or n in seen:
            continue
        seen.add(n)
        out.append(n)
        if len(out) >= 20:
            break
    return out

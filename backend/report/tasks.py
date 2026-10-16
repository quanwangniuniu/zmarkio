from celery import shared_task
from django.utils import timezone

from report.cross_platform import build_cross_platform_report
from report.models import CrossPlatformReportDefinition


def rollup_definition(definition: CrossPlatformReportDefinition) -> dict:
    payload = build_cross_platform_report(
        definition.project_id,
        platforms=definition.platforms,
        lookback_days=definition.lookback_days,
    )
    now = timezone.now()
    payload["last_updated_at"] = now.isoformat()
    definition.rolled_up_data = payload
    definition.last_rolled_up_at = now
    definition.save(
        update_fields=["rolled_up_data", "last_rolled_up_at", "updated_at"]
    )
    return payload


@shared_task(name="report.tasks.rollup_all_cross_platform_reports")
def rollup_all_cross_platform_reports() -> int:
    definitions = CrossPlatformReportDefinition.objects.filter(is_active=True)
    for definition in definitions.iterator():
        rollup_definition(definition)
    return definitions.count()

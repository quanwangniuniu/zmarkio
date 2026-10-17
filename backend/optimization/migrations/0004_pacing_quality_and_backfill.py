from django.db import migrations, models
from django.utils import timezone


def backfill_pacing_rows(apps, schema_editor):
    """Ensure every existing active campaign has a deterministic placeholder.

    Spend-provider queries stay out of migrations. The normal nightly job
    replaces these placeholders with live forecasts after deployment.
    """
    Campaign = apps.get_model("campaign", "Campaign")
    Forecast = apps.get_model("optimization", "CampaignPacingForecast")
    today = timezone.now().date()

    pending = []
    campaigns = Campaign.objects.filter(is_deleted=False).only(
        "id", "budget_estimate", "start_date", "end_date"
    )
    for campaign in campaigns.iterator(chunk_size=500):
        if campaign.start_date is None:
            status, reason = "not_configured", "missing_start_date"
        elif campaign.budget_estimate is None and campaign.end_date is None:
            status, reason = "not_configured", "missing_budget_and_end_date"
        elif campaign.budget_estimate is None:
            status, reason = "not_configured", "missing_budget"
        elif campaign.end_date is None:
            status, reason = "not_configured", "missing_end_date"
        elif campaign.end_date < campaign.start_date:
            status, reason = "not_configured", "invalid_period"
        elif today < campaign.start_date:
            status, reason = "not_started", ""
        else:
            status, reason = "no_data", "no_linked_spend"

        pending.append(Forecast(
            campaign_id=campaign.id,
            status=status,
            reason=reason,
            data_quality="not_applicable",
            computed_for_date=today,
        ))
        if len(pending) == 500:
            Forecast.objects.bulk_create(pending, ignore_conflicts=True)
            pending = []

    if pending:
        Forecast.objects.bulk_create(pending, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("campaign", "0006_campaign_budget_estimate_positive"),
        ("optimization", "0003_campaignpacingforecast"),
    ]

    operations = [
        migrations.AlterField(
            model_name="campaignpacingforecast",
            name="reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("missing_budget", "Missing Budget"),
                    ("missing_start_date", "Missing Start Date"),
                    ("missing_end_date", "Missing End Date"),
                    ("missing_budget_and_end_date", "Missing Budget And End Date"),
                    ("invalid_period", "Invalid Period"),
                    ("no_linked_spend", "No Linked Spend"),
                    ("no_spend_recorded", "No Spend Recorded"),
                    ("unsupported_spend_platform", "Unsupported Spend Platform"),
                ],
                default="",
                help_text="Why no actionable forecast is available (blank when one is)",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="campaignpacingforecast",
            name="data_quality",
            field=models.CharField(
                choices=[
                    ("complete", "Complete"),
                    ("partial", "Partial"),
                    ("unsupported", "Unsupported"),
                    ("not_applicable", "Not Applicable"),
                ],
                default="not_applicable",
                help_text="Whether spend covers every configured campaign platform",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="campaignpacingforecast",
            name="spend_platforms",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Configured platforms whose spend is included in the forecast",
            ),
        ),
        migrations.AddField(
            model_name="campaignpacingforecast",
            name="unsupported_platforms",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Configured platforms with no implemented spend provider",
            ),
        ),
        migrations.RunPython(backfill_pacing_rows, migrations.RunPython.noop),
    ]

from django.db import migrations, models
import django.db.models.deletion
import report.models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_merge_0023_sessions_and_ai"),
        ("report", "0005_reportsharelink"),
    ]

    operations = [
        migrations.CreateModel(
            name="CrossPlatformReportDefinition",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "platforms",
                    models.JSONField(
                        blank=True, default=report.models.default_cross_platforms
                    ),
                ),
                ("lookback_days", models.PositiveSmallIntegerField(default=30)),
                ("is_active", models.BooleanField(default=True)),
                ("rolled_up_data", models.JSONField(blank=True, default=dict)),
                ("last_rolled_up_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "project",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cross_platform_report",
                        to="core.project",
                    ),
                ),
            ],
            options={"db_table": "report_cross_platform_definition"},
        ),
        migrations.CreateModel(
            name="AdsDraftPerformanceDaily",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "platform",
                    models.CharField(
                        choices=[
                            ("facebook_meta", "Facebook Meta"),
                            ("google_ads", "Google Ads"),
                            ("tiktok", "TikTok"),
                        ],
                        max_length=20,
                    ),
                ),
                ("date", models.DateField(db_index=True)),
                (
                    "metrics",
                    models.JSONField(
                        default=dict,
                        help_text=(
                            "Raw platform metrics. Facebook Meta: amount_spent, "
                            "impressions, clicks, purchase_value. Google Ads: "
                            "cost_micros, impressions, clicks, conversions_value. "
                            "TikTok: spend, impressions, clicks, "
                            "total_purchase_value."
                        ),
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ads_draft_performance",
                        to="core.project",
                    ),
                ),
            ],
            options={
                "db_table": "report_ads_draft_performance_daily",
                "ordering": ["date", "platform"],
            },
        ),
        migrations.AddConstraint(
            model_name="adsdraftperformancedaily",
            constraint=models.UniqueConstraint(
                fields=("project", "platform", "date"),
                name="uniq_ads_draft_performance_day",
            ),
        ),
    ]

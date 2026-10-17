from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


def clear_invalid_budgets(apps, schema_editor):
    """Old zero/negative values mean "not configured", so preserve that meaning."""
    Campaign = apps.get_model("campaign", "Campaign")
    Campaign.objects.filter(budget_estimate__lte=0).update(budget_estimate=None)


class Migration(migrations.Migration):

    dependencies = [
        ("campaign", "0005_campaigntemplate_slug"),
    ]

    operations = [
        migrations.RunPython(clear_invalid_budgets, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="campaign",
            name="budget_estimate",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Budget estimate (optional)",
                max_digits=15,
                null=True,
                validators=[MinValueValidator(Decimal("0.01"))],
            ),
        ),
        migrations.AddConstraint(
            model_name="campaign",
            constraint=models.CheckConstraint(
                check=(
                    models.Q(("budget_estimate__isnull", True))
                    | models.Q(("budget_estimate__gt", 0))
                ),
                name="campaign_budget_estimate_positive",
            ),
        ),
    ]

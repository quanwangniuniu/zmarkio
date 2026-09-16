from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("calendars", "0009_bookinglink_invitees_only"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookinglink",
            name="impact_review",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]

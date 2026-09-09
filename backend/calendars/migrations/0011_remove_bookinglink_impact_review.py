from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("calendars", "0010_bookinglink_impact_review"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="bookinglink",
            name="impact_review",
        ),
    ]

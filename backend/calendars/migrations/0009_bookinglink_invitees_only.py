from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("calendars", "0008_attendee_phone_and_multiple_invitees"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookinglink",
            name="invitees_only",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "When set, only named invitees who are signed in can book. "
                    "Guests and anyone else with the URL cannot."
                ),
            ),
        ),
    ]

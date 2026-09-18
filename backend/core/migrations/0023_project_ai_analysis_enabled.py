from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0022_widen_password_rotation_clock_skew'),
    ]

    operations = [
        migrations.AddField(
            model_name='project',
            name='ai_analysis_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Whether AI-assisted spreadsheet analysis is available for this project',
            ),
        ),
    ]

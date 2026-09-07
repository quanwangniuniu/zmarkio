# Per-spreadsheet AI-analysis consent (replaces core.ProjectMember.ai_consent_at).

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('spreadsheet', '0011_sheet_revision'),
    ]

    operations = [
        migrations.CreateModel(
            name='SpreadsheetAiConsent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('spreadsheet', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ai_consents', to='spreadsheet.spreadsheet')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='spreadsheet_ai_consents', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='spreadsheetaiconsent',
            index=models.Index(fields=['spreadsheet', 'user'], name='sheet_ai_consent_lookup_idx'),
        ),
        migrations.AddConstraint(
            model_name='spreadsheetaiconsent',
            constraint=models.UniqueConstraint(fields=('user', 'spreadsheet'), name='unique_spreadsheet_ai_consent_per_user'),
        ),
    ]

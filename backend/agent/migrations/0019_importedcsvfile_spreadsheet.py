from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('spreadsheet', '0001_initial'),
        ('agent', '0018_agentworkflowrun_created_decisions'),
    ]

    operations = [
        migrations.AddField(
            model_name='importedcsvfile',
            name='spreadsheet',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='imported_csv_files',
                to='spreadsheet.spreadsheet',
            ),
        ),
    ]

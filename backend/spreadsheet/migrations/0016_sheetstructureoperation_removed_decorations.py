from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('spreadsheet', '0015_sheetimportsource_baseline'),
    ]

    operations = [
        migrations.AddField(
            model_name='sheetstructureoperation',
            name='removed_decorations',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Formats and highlights removed by a row or column delete',
            ),
        ),
    ]
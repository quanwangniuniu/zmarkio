from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('spreadsheet', '0014_sheetimportsource'),
    ]

    operations = [
        migrations.AddField(
            model_name='sheetimportsource',
            name='baseline',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('spreadsheet', '0016_sheetstructureoperation_removed_decorations'),
    ]

    operations = [
        migrations.AddField(
            model_name='sheetstructureoperation',
            name='formula_rewrites',
            field=models.JSONField(
                blank=True,
                default=None,
                help_text='Formula text replaced by this structural operation, keyed by cell id',
                null=True,
            ),
        ),
    ]
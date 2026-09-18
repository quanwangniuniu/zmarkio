from django.db import migrations, models
import django.contrib.postgres.indexes
import django.contrib.postgres.search


class Migration(migrations.Migration):

    atomic = False  # required: CREATE INDEX CONCURRENTLY cannot run inside a transaction

    dependencies = [
        ("meetings", "0020_merge_meeting_slug_audit"),
    ]

    operations = [
        migrations.AddField(
            model_name="meeting",
            name="transcript",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Plain-text Zoom transcript for full-text search.",
            ),
        ),
        migrations.AddField(
            model_name="meeting",
            name="search_vector",
            field=django.contrib.postgres.search.SearchVectorField(
                blank=True, null=True
            ),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="meeting",
                    index=django.contrib.postgres.indexes.GinIndex(
                        fields=["search_vector"], name="mtgs_mtg_search_vec"
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="CREATE INDEX CONCURRENTLY IF NOT EXISTS mtgs_mtg_search_vec "
                        "ON meetings_meeting USING gin(search_vector)",
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS mtgs_mtg_search_vec",
                ),
            ],
        ),
    ]

import django.db.models.deletion
import pgvector.django
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0023_project_ai_analysis_enabled'),
    ]

    operations = [
        # Enables the Postgres `vector` extension. See postgres/Dockerfile
        # (CI/bundled Postgres) and DOCKER_README.md (local/host Postgres)
        # for where the extension package itself must be installed — this
        # statement only activates it in the current database, it can't
        # fetch the extension binary if the OS package is missing.
        pgvector.django.VectorExtension(),
        migrations.CreateModel(
            name='DocumentChunk',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('source_type', models.CharField(choices=[('meeting', 'Meeting'), ('notion_draft', 'Notion Draft'), ('retrospective', 'Retrospective')], max_length=20)),
                ('source_id', models.CharField(help_text="String form of the source row's PK (int or UUID depending on source_type).", max_length=64)),
                ('chunk_index', models.PositiveIntegerField(help_text='Position of this chunk within the source document, for stable re-assembly/citation.')),
                ('content', models.TextField()),
                # dimensions is a literal here (not settings.RAG_EMBEDDING_DIMENSIONS)
                # because migrations must be deterministic at apply time. It mirrors
                # RAG_EMBEDDING_DIMENSIONS as of this migration's authoring; changing
                # that setting later requires a new migration to match, by design.
                ('embedding', pgvector.django.VectorField(blank=True, dimensions=768, help_text='Null until the indexing pipeline embeds this chunk.', null=True)),
                ('source_updated_at', models.DateTimeField(help_text="updated_at of the source row as of this chunk's last (re-)embedding. The indexer compares this against the source's live updated_at to decide whether the document is unchanged and can be skipped, or must be re-chunked.")),
                ('citation_metadata', models.JSONField(blank=True, default=dict, help_text='Source-specific fields needed to render a citation (e.g. title, url, timestamp).')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='document_chunks', to='core.project')),
            ],
        ),
        migrations.AddIndex(
            model_name='documentchunk',
            index=models.Index(fields=['project', 'source_type', 'source_id'], name='rag_chunk_prj_src'),
        ),
        migrations.AddConstraint(
            model_name='documentchunk',
            constraint=models.UniqueConstraint(fields=('project', 'source_type', 'source_id', 'chunk_index'), name='rag_chunk_unique_source_position'),
        ),
    ]

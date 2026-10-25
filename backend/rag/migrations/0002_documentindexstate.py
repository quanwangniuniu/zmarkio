import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0023_project_ai_analysis_enabled'),
        ('rag', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='DocumentIndexState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_type', models.CharField(choices=[('meeting', 'Meeting'), ('notion_draft', 'Notion Draft'), ('retrospective', 'Retrospective')], max_length=20)),
                ('source_id', models.CharField(max_length=64)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('complete', 'Complete'), ('failed', 'Failed')], default='pending', max_length=10)),
                ('indexed_content_hash', models.CharField(blank=True, help_text='sha256 of the extracted text as of the last successful index. Null if never successfully indexed (or currently excluded).', max_length=64, null=True)),
                ('indexed_pipeline_hash', models.CharField(blank=True, help_text='Fingerprint of the indexing pipeline config as of the last successful index (see rag.indexing.current_pipeline_hash).', max_length=64, null=True)),
                ('indexed_source_updated_at', models.DateTimeField(blank=True, help_text="Observability only: the source row's updated_at as of the last successful index. Not used for the skip decision.", null=True)),
                ('last_error', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='document_index_states', to='core.project')),
            ],
        ),
        migrations.AddConstraint(
            model_name='documentindexstate',
            constraint=models.UniqueConstraint(fields=('project', 'source_type', 'source_id'), name='rag_index_state_unique_source'),
        ),
    ]

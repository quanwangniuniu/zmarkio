import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0023_project_ai_analysis_enabled'),
        ('notion_editor', '0004_draft_slug'),
    ]

    operations = [
        migrations.CreateModel(
            name='DraftProjectLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('draft', models.OneToOneField(help_text='The draft assigned to a project.', on_delete=django.db.models.deletion.CASCADE, related_name='project_link', to='notion_editor.draft')),
                ('project', models.ForeignKey(help_text='The project this draft is assigned to.', on_delete=django.db.models.deletion.CASCADE, related_name='draft_links', to='core.project')),
            ],
        ),
    ]

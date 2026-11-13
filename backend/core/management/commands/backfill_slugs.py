from django.core.management.base import BaseCommand
from django.apps import apps
from django.utils.text import slugify

class Command(BaseCommand):
    help = 'Backfills slug fields for all SluggedResourceModelMixin models'

    def handle(self, *args, **options):
        # We know exactly which models implement SluggedResourceModelMixin
        target_models = [
            'task.Task',
            'spreadsheet.Spreadsheet',
            'meetings.Meeting',
            'decision.Decision',
            'campaign.Campaign',
            'workflows.Workflow',
            'core.Project',
            'miro.Board',
            'mailchimp.Campaign',
            'klaviyo.EmailDraft',
            'notion_editor.Draft',
            'csm.QuickReplyTemplate',
        ]
        
        for model_path in target_models:
            app_label, model_name = model_path.split('.')
            try:
                Model = apps.get_model(app_label, model_name)
            except LookupError:
                self.stdout.write(self.style.WARNING(f"Model {model_path} not found"))
                continue
                
            if not hasattr(Model, 'slug'):
                self.stdout.write(self.style.WARNING(f"Model {model_path} does not have a slug field"))
                continue
                
            self.stdout.write(f"Backfilling {model_path}...")
            
            from django.db.models import Q
            # Fetch objects without slugs
            qs = Model.objects.filter(Q(slug__isnull=True) | Q(slug='') | Q(slug__startswith='temp-empty-'))
            count = qs.count()
            
            if count == 0:
                self.stdout.write(self.style.SUCCESS(f"No objects to backfill for {model_path}"))
                continue
                
            for obj in qs:
                if obj.slug and obj.slug.startswith('temp-empty-'):
                    obj.slug = ''
                obj.save() # The mixin's save method will handle slug generation
                
            self.stdout.write(self.style.SUCCESS(f"Successfully backfilled {count} {model_path} objects"))


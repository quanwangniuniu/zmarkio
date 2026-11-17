from django.core.management.base import BaseCommand

from core.models import Organization, Project
from core.services.tenant import slug_to_schema_name
from core.tenant_context import tenant_schema_context
from org_customization.services import seed_task_config


class Command(BaseCommand):
    help = "Create the default task config rows for existing projects. Existing rows are kept."

    def handle(self, *args, **options):
        seeded = 0
        for project in Project.objects.iterator(chunk_size=200):
            seed_task_config(project)
            seeded += 1
        for organization in Organization.objects.order_by('slug').iterator(chunk_size=200):
            with tenant_schema_context(slug_to_schema_name(organization.slug)):
                for project in Project.objects.filter(organization_id=organization.id).iterator(chunk_size=200):
                    seed_task_config(project)
                    seeded += 1
        self.stdout.write(f"Done. Seeded task config for {seeded} projects.")

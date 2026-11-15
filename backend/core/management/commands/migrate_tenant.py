"""
Management command: migrate_tenant

Creates (or updates) Django-managed tenant tables for a single Organization.
For variation tables, run `npm run migrate` in variations-studio-api separately.

Usage
-----
# First-time provisioning after manual org creation (rare):
python manage.py migrate_tenant --slug=acme-corp

# Add tables that were missing (e.g. added to tenant_config after the org
# was provisioned):
python manage.py migrate_tenant --slug=acme-corp

Behaviour
---------
- If the schema does not yet exist, CREATE SCHEMA is issued first.
- For each model in tenant_config.get_tenant_models(), if the table is
  missing from the target schema it is created via Django SchemaEditor.
- Missing columns on registered tables are added (idempotent).

When to run
-----------
Normally you do NOT need this command: Organization.save() provisions the
schema automatically on creation.  Use this command only for:
  * Manually created orgs that bypassed save() (e.g. data imports).
  * Orgs created before this feature was deployed (legacy).
  * Adding new models to an existing schema after tenant_config is extended.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from psycopg2 import sql as psql

from core.services.tenant import (
    _create_tenant_tables,
    slug_to_schema_name,
)


class Command(BaseCommand):
    help = 'Provision (or repair) Django-managed tenant tables for a single org slug.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--slug',
            required=True,
            help='The Organization.slug value (e.g. "acme-corp").',
        )

    def handle(self, *args, **options):
        slug = options['slug']

        # Validate the org exists
        from core.models import Organization
        if not Organization.objects.filter(slug=slug).exists():
            raise CommandError(
                f'No Organization found with slug="{slug}". '
                'Check the slug or create the org first.'
            )

        schema_name = slug_to_schema_name(slug)
        self.stdout.write(f'Target schema: {schema_name}')

        # Ensure schema exists
        with connection.cursor() as cursor:
            cursor.execute(
                psql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(
                    psql.Identifier(schema_name)
                )
            )
        self.stdout.write(f'  Schema ensured: {schema_name}')

        # Create / repair tables
        self.stdout.write('  Creating missing tenant tables...')
        _create_tenant_tables(schema_name)

        self.stdout.write(self.style.SUCCESS(
            f'Done. Django-managed tables in "{schema_name}" are up to date.'
        ))

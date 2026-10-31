"""
Management command: migrate_all_tenants

Repairs Django-managed tables in existing schemas for active Organizations.
For variation tables, run `npm run migrate` in variations-studio-api separately.

Usage
-----
# After extending tenant_config.py with new models:
python manage.py migrate_all_tenants

# Dry-run (shows what would happen without writing anything):
python manage.py migrate_all_tenants --dry-run

# Stop on first error instead of continuing:
python manage.py migrate_all_tenants --fail-fast

# Limit to specific orgs:
python manage.py migrate_all_tenants --slug acme-corp --slug beta-inc

When to run
-----------
Run this command after every deployment that adds new models to
tenant_config.py, to ensure existing org schemas are brought up to date.
New orgs provisioned after deployment pick up the new models automatically
via Organization.save().
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection
from psycopg2 import sql as psql

from core.services.tenant import (
    _create_tenant_tables,
    _table_exists,
    slug_to_schema_name,
)


def _schema_exists(schema_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.schemata
                WHERE schema_name = %s
            )
            """,
            [schema_name],
        )
        return cursor.fetchone()[0]


class Command(BaseCommand):
    help = (
        'Repair Django-managed tenant tables for all active Organizations. '
        'Safe to run multiple times (idempotent).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--slug',
            dest='slugs',
            action='append',
            metavar='SLUG',
            help=(
                'Limit execution to this org slug. '
                'Can be repeated: --slug acme --slug beta'
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without writing to the database.',
        )
        parser.add_argument(
            '--fail-fast',
            action='store_true',
            help='Stop immediately when an error is encountered.',
        )

    def handle(self, *args, **options):
        from core.models import Organization

        slugs_filter: list[str] | None = options.get('slugs')
        dry_run: bool = options['dry_run']
        fail_fast: bool = options['fail_fast']

        qs = Organization.objects.filter(is_active=True).order_by('slug')
        if slugs_filter:
            qs = qs.filter(slug__in=slugs_filter)

        total = qs.count()
        self.stdout.write(f'Organizations to process: {total}')

        if dry_run:
            self.stdout.write(
                self.style.WARNING('Dry-run mode — no changes will be written.')
            )

        processed = skipped = errors = 0

        for org in qs.iterator(chunk_size=200):
            schema_name = slug_to_schema_name(org.slug)
            self.stdout.write(f'  [{processed + skipped + errors + 1}/{total}] '
                              f'{org.slug} → {schema_name}', ending=' ')

            # Check schema existence
            if not _schema_exists(schema_name):
                self.stdout.write('')
                self.stdout.write(
                    self.style.WARNING(
                        f'    Schema "{schema_name}" does not exist — skipping. '
                        'Run `migrate_tenant --slug={org.slug}` to create it.'
                    )
                )
                skipped += 1
                continue

            if dry_run:
                self.stdout.write('(dry-run, skipped)')
                processed += 1
                continue

            try:
                _create_tenant_tables(schema_name)
                self.stdout.write(self.style.SUCCESS('✓'))
                processed += 1
            except Exception as exc:
                self.stdout.write('')
                self.stderr.write(
                    f'    ERROR processing {schema_name}: {exc}'
                )
                errors += 1
                if fail_fast:
                    raise

        self.stdout.write('')
        self.stdout.write(f'Results: {processed} updated, {skipped} skipped, {errors} errors')

        if dry_run:
            self.stdout.write(self.style.SUCCESS('Dry-run complete.'))
        elif errors == 0:
            self.stdout.write(self.style.SUCCESS('Django-managed tenant tables up to date.'))
        else:
            self.stdout.write(
                self.style.ERROR(f'{errors} tenant(s) failed. Review errors above.')
            )

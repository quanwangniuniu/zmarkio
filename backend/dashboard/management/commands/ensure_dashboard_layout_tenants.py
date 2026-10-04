"""Plan or create only the MED-287 table in existing organization schemas."""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from psycopg2 import sql as psql

from core.models import Organization
from core.services.tenant import lock_tenant_provisioning, slug_to_schema_name
from dashboard.models import DashboardLayout


def table_exists(schema, table):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = %s)",
            [schema, table],
        )
        return cursor.fetchone()[0]


def schema_exists(schema):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = %s)",
            [schema],
        )
        return cursor.fetchone()[0]


class Command(BaseCommand):
    help = 'Plan or create only dashboard_dashboardlayout in existing org schemas.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Create missing dashboard layout tables; default is read-only plan.')
        parser.add_argument('--slug', action='append', dest='slugs', help='Limit to an organization slug; repeatable.')

    def handle(self, *args, **options):
        table = DashboardLayout._meta.db_table
        if options['apply'] and not table_exists('public', table):
            raise CommandError('Public dashboard layout table is missing. Run migrate dashboard first.')

        organizations = Organization.objects.order_by('slug')
        if options['slugs']:
            organizations = organizations.filter(slug__in=options['slugs'])
            found = set(organizations.values_list('slug', flat=True))
            missing = set(options['slugs']) - found
            if missing:
                raise CommandError(f'Unknown organization slug(s): {", ".join(sorted(missing))}')

        created = existing = 0
        for org in organizations.iterator():
            schema = slug_to_schema_name(org.slug)
            if not schema_exists(schema):
                raise CommandError(f'{org.slug}: schema {schema} is missing; no changes made to it.')
            if not table_exists(schema, 'core_project'):
                raise CommandError(f'{org.slug}: core_project is missing in {schema}; no changes made to it.')
            if table_exists(schema, table):
                self.stdout.write(f'{org.slug}: already present')
                existing += 1
                continue
            if not options['apply']:
                self.stdout.write(f'{org.slug}: would create {schema}.{table}')
                continue

            with transaction.atomic():
                lock_tenant_provisioning()
                if table_exists(schema, table):
                    self.stdout.write(f'{org.slug}: already present')
                    existing += 1
                    continue
                with connection.cursor() as cursor:
                    cursor.execute('SHOW search_path')
                    previous_path = cursor.fetchone()[0]
                    cursor.execute(
                        psql.SQL('SET search_path TO {}, public').format(psql.Identifier(schema))
                    )
                # Provisioning uses the same SchemaEditor pattern. It creates
                # only DashboardLayout, including its unique constraint and FKs.
                with connection.schema_editor(atomic=False) as editor:
                    editor.create_model(DashboardLayout)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT set_config('search_path', %s, false)", [previous_path])
            self.stdout.write(self.style.SUCCESS(f'{org.slug}: created {schema}.{table}'))
            created += 1

        if options['apply']:
            self.stdout.write(f'Created: {created}; already present: {existing}')
        else:
            self.stdout.write(f'Read-only plan complete; already present: {existing}. Re-run with --apply after backup.')

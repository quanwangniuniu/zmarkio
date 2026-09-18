import uuid

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase

from ad_copy_variation.models import AdCopyVariation
from ad_copy_variation.tenant_backfill import (
    VARIATION_TABLE,
    backfill_variations_to_tenants,
)
from core.models import Organization, Project
from core.services.tenant import slug_to_schema_name


User = get_user_model()


def _set_path(schema='public'):
    with connection.cursor() as cursor:
        if schema == 'public':
            cursor.execute('SET search_path TO public')
        else:
            cursor.execute('SET search_path TO %s, public', [schema])


def _count(schema, table, where='', params=None):
    sql = f'SELECT COUNT(*) FROM "{schema}"."{table}"'
    if where:
        sql += f' WHERE {where}'
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        return cursor.fetchone()[0]


class TenantBackfillTests(TestCase):
    def setUp(self):
        _set_path('public')
        suffix = uuid.uuid4().hex[:8]
        self.org = Organization.objects.create(
            name=f'Backfill Org {suffix}',
            slug=f'backfill-org-{suffix}',
        )
        self.schema = slug_to_schema_name(self.org.slug)
        self.user = User.objects.create_user(
            username=f'backfill-{suffix}',
            email=f'backfill-{suffix}@example.com',
            password='x',
        )
        self.project_slug = f'shared-campaign-{suffix}'

    def tearDown(self):
        _set_path('public')

    def _project_in(self, schema, name):
        """Insert a project with an explicit slug so public/org cannot diverge."""
        _set_path(schema if schema != 'public' else 'public')
        # When search_path is org,public the uniqueness check would otherwise
        # see the public row and suffix the org slug.
        try:
            project = Project(
                name=name,
                organization=self.org,
                owner=self.user,
                slug=self.project_slug,
            )
            project.save()
            return project
        finally:
            _set_path('public')

    def _public_variation(self, project, headline):
        _set_path('public')
        return AdCopyVariation.objects.create(
            project=project,
            source_mode='custom',
            headline=headline,
            hook='h',
            description='d',
            created_by=self.user,
        )

    def test_copies_row_and_remaps_project_by_slug(self):
        public_project = self._project_in('public', 'Shared Campaign')
        # Pad the org schema so its project pk sequence is pushed past the
        # public pk. The public sequence is process-global and not rolled back
        # between tests, so public_project.id is not necessarily 1 — pad enough
        # rows that the org project cannot land on the same pk.
        _set_path(self.schema)
        try:
            for _ in range(public_project.id):
                Project(
                    name='Padding',
                    organization=self.org,
                    owner=self.user,
                    slug=f'padding-{uuid.uuid4().hex[:8]}',
                ).save()
        finally:
            _set_path('public')
        org_project = self._project_in(self.schema, 'Shared Campaign')
        self.assertNotEqual(public_project.id, org_project.id)
        self.assertEqual(public_project.slug, org_project.slug)

        row = self._public_variation(public_project, 'Migrated headline')

        result = backfill_variations_to_tenants(org_slugs=[self.org.slug])

        self.assertEqual(result.copied, 1)
        self.assertEqual(result.skipped, 0)
        self.assertEqual(
            _count(self.schema, VARIATION_TABLE, 'slug = %s', [row.slug]),
            1,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT project_id FROM "{self.schema}"."{VARIATION_TABLE}" WHERE slug = %s',
                [row.slug],
            )
            self.assertEqual(cursor.fetchone()[0], org_project.id)

    def test_dry_run_writes_nothing(self):
        public_project = self._project_in('public', 'Shared Campaign')
        self._project_in(self.schema, 'Shared Campaign')
        self._public_variation(public_project, 'Dry run headline')

        result = backfill_variations_to_tenants(
            org_slugs=[self.org.slug],
            dry_run=True,
        )

        self.assertEqual(result.copied, 1)
        self.assertEqual(_count(self.schema, VARIATION_TABLE), 0)

    def test_skips_when_project_slug_missing_in_org(self):
        public_project = self._project_in('public', 'Only In Public')
        self._public_variation(public_project, 'Orphan headline')

        result = backfill_variations_to_tenants(org_slugs=[self.org.slug])

        self.assertEqual(result.copied, 0)
        self.assertEqual(result.reasons.get('project_slug_not_in_org'), 1)
        self.assertEqual(_count(self.schema, VARIATION_TABLE), 0)

    def test_second_run_is_idempotent(self):
        public_project = self._project_in('public', 'Shared Campaign')
        self._project_in(self.schema, 'Shared Campaign')
        self._public_variation(public_project, 'Once only')

        first = backfill_variations_to_tenants(org_slugs=[self.org.slug])
        second = backfill_variations_to_tenants(org_slugs=[self.org.slug])

        self.assertEqual(first.copied, 1)
        self.assertEqual(second.copied, 0)
        self.assertEqual(second.reasons.get('already_copied'), 1)
        self.assertEqual(_count(self.schema, VARIATION_TABLE), 1)

    def test_management_command_dry_run(self):
        public_project = self._project_in('public', 'Shared Campaign')
        self._project_in(self.schema, 'Shared Campaign')
        self._public_variation(public_project, 'Command headline')

        call_command(
            'migrate_ad_copy_variations_to_tenants',
            '--dry-run',
            '--slug',
            self.org.slug,
        )
        self.assertEqual(_count(self.schema, VARIATION_TABLE), 0)

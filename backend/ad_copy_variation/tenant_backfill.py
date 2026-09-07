"""
One-shot public → org copy of AdCopyVariation rows.

Org is taken from public.core_project.organization_id. The destination
project is the org-schema row with the same slug — never the public pk.
Rows that cannot be placed are left in public and counted as skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import connection
from psycopg2 import sql as psql

from core.services.tenant import slug_to_schema_name

VARIATION_TABLE = 'ad_copy_variation_adcopyvariation'

COPY_COLUMNS = (
    'created_at',
    'updated_at',
    'is_deleted',
    'source_mode',
    'source_ref',
    'hook',
    'headline',
    'description',
    'cta',
    'instruction',
    'model_name',
    'prompt_version',
    'batch_id',
    'batch_position',
    'status',
    'created_by_id',
    'creative_id',
    'slug',
)


@dataclass
class BackfillResult:
    copied: int = 0
    skipped: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1


def _ident(*parts: str) -> psql.Composed:
    return psql.SQL('.').join(psql.Identifier(part) for part in parts)


def _schema_exists(schema: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)',
            [schema],
        )
        return cursor.fetchone()[0]


def _table_exists(schema: str, table: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXISTS (
              SELECT 1 FROM information_schema.tables
              WHERE table_schema = %s AND table_name = %s
            )
            """,
            [schema, table],
        )
        return cursor.fetchone()[0]


def _org_project_id(schema: str, slug: str) -> int | None:
    query = psql.SQL(
        'SELECT id FROM {} WHERE slug = %s AND is_deleted = false LIMIT 1'
    ).format(_ident(schema, 'core_project'))
    with connection.cursor() as cursor:
        cursor.execute(query, [slug])
        row = cursor.fetchone()
    return row[0] if row else None


def _slug_already_in_org(schema: str, slug: str) -> bool:
    query = psql.SQL('SELECT EXISTS (SELECT 1 FROM {} WHERE slug = %s)').format(
        _ident(schema, VARIATION_TABLE)
    )
    with connection.cursor() as cursor:
        cursor.execute(query, [slug])
        return cursor.fetchone()[0]


def _public_rows(org_slugs: list[str] | None) -> list[dict]:
    extra = ''
    params: list = []
    if org_slugs:
        extra = 'AND o.slug = ANY(%s)'
        params.append(org_slugs)

    query = f"""
        SELECT
          v.id,
          v.slug,
          v.project_id,
          p.slug AS project_slug,
          p.organization_id,
          o.slug AS org_slug
        FROM public.{VARIATION_TABLE} v
        LEFT JOIN public.core_project p ON p.id = v.project_id
        LEFT JOIN public.core_organization o ON o.id = p.organization_id
        WHERE TRUE {extra}
        ORDER BY v.id
    """
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _copy_row(schema: str, public_id: int, dest_project_id: int) -> None:
    dest = _ident(schema, VARIATION_TABLE)
    src = _ident('public', VARIATION_TABLE)
    columns = psql.SQL(', ').join(psql.Identifier(col) for col in COPY_COLUMNS)
    select_columns = psql.SQL(', ').join(
        psql.SQL('src.{}').format(psql.Identifier(col)) for col in COPY_COLUMNS
    )
    query = psql.SQL(
        """
        INSERT INTO {dest} (project_id, {columns})
        SELECT %s, {select_columns}
        FROM {src} AS src
        WHERE src.id = %s
        """
    ).format(dest=dest, columns=columns, select_columns=select_columns, src=src)
    with connection.cursor() as cursor:
        cursor.execute(query, [dest_project_id, public_id])


def backfill_variations_to_tenants(
    *,
    org_slugs: list[str] | None = None,
    dry_run: bool = False,
) -> BackfillResult:
    result = BackfillResult()
    missing_table_orgs: set[str] = set()

    for row in _public_rows(org_slugs):
        if row['project_id'] is None:
            result.skip('missing_project')
            continue
        if not row['organization_id'] or not row['org_slug']:
            result.skip('no_organization')
            continue

        schema = slug_to_schema_name(row['org_slug'])
        if not _schema_exists(schema):
            result.skip('missing_schema')
            continue
        if schema in missing_table_orgs or not _table_exists(schema, VARIATION_TABLE):
            missing_table_orgs.add(schema)
            result.skip('missing_variation_table')
            continue

        dest_project_id = _org_project_id(schema, row['project_slug'])
        if dest_project_id is None:
            result.skip('project_slug_not_in_org')
            continue
        if _slug_already_in_org(schema, row['slug']):
            result.skip('already_copied')
            continue

        if not dry_run:
            _copy_row(schema, row['id'], dest_project_id)
        result.copied += 1

    return result

"""
Tenant schema provisioning utilities.

All PostgreSQL schema operations (CREATE SCHEMA, SET search_path, table creation)
are centralised here so that no other module needs to know about schema names
or safe identifier quoting.

Usage:
    Called exclusively from Organization.save() inside a transaction.atomic() block.
    The caller owns the transaction; DDL failures roll back both the schema DDL
    and the Organization INSERT atomically (PostgreSQL DDL is transactional).
"""

import re

from django.db import connection
from psycopg2 import sql as psql


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def slug_to_schema_name(slug: str) -> str:
    """
    Convert an org slug to a valid PostgreSQL schema name.

    Rules applied:
    - Replace any non-alphanumeric character with underscore
    - Prefix with 'org_'

    Examples:
        'acme-corp'  -> 'org_acme_corp'
        'my.company' -> 'org_my_company'
        'Test Org'   -> 'org_Test_Org'
    """
    safe = re.sub(r'[^a-zA-Z0-9]', '_', slug)
    return f"org_{safe}"


def rename_tenant_schema(old_slug: str, new_slug: str) -> None:
    """
    Atomically rename a tenant's PostgreSQL schema when its slug changes.

    MUST be called inside a transaction.atomic() block so that the schema
    rename and the slug UPDATE on the Organization row are committed together
    or rolled back together.

    Args:
        old_slug: The current Organization.slug value (maps to current schema).
        new_slug: The desired new slug (maps to target schema name).
    """
    old_schema = slug_to_schema_name(old_slug)
    new_schema = slug_to_schema_name(new_slug)

    if old_schema == new_schema:
        return  # normalised names are identical — nothing to do

    with connection.cursor() as cursor:
        cursor.execute(
            psql.SQL('ALTER SCHEMA {} RENAME TO {}').format(
                psql.Identifier(old_schema),
                psql.Identifier(new_schema),
            )
        )


def provision_tenant_schema(slug: str) -> None:
    """
    Create a PostgreSQL schema and populate it with all tenant tables.

    MUST be called from within a transaction.atomic() block (enforced by
    Organization.save()). If CREATE SCHEMA or any table creation fails, the
    surrounding atomic() rolls back both the DDL and the org INSERT atomically.

    This is intentionally synchronous (not Celery): schema creation must be
    atomic with the Organization record. Org creation is a low-frequency
    operation, so the extra latency (typically < 5 s) is acceptable.

    Args:
        slug: The Organization.slug value (already validated and set before
              this function is called).
    """
    schema_name = slug_to_schema_name(slug)

    with connection.cursor() as cursor:
        # CREATE SCHEMA requires an SQL identifier, not a string literal.
        # psycopg2.sql.Identifier correctly double-quotes the name, preventing
        # SQL injection and handling names that would otherwise be invalid.
        cursor.execute(
            psql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(
                psql.Identifier(schema_name)
            )
        )

    _create_tenant_tables(schema_name)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _create_tenant_tables(schema_name: str) -> None:
    """
    Use Django's SchemaEditor to create all tenant model tables inside the
    given schema. search_path is set for the duration of this call and
    reset in the finally block to prevent connection pool pollution.

    SchemaEditor.create_model() issues CREATE TABLE, adds indexes and
    constraints — all within the current transaction so failures roll back.
    """
    from core.tenant_config import get_tenant_models

    # SET search_path accepts string literals (%s), so psycopg2 quoting is safe.
    with connection.cursor() as cursor:
        cursor.execute('SET search_path TO %s, public', [schema_name])

    try:
        with connection.schema_editor() as editor:
            for model in get_tenant_models():
                if not _table_exists(model._meta.db_table, schema_name):
                    editor.create_model(model)
                else:
                    _add_missing_columns(editor, model, schema_name)
    finally:
        # Always reset to public so the connection is returned clean to the pool.
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO public')


def _existing_columns(table_name: str, schema_name: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            """,
            [schema_name, table_name],
        )
        return {row[0] for row in cursor.fetchall()}


def _add_missing_columns(editor, model, schema_name: str) -> None:
    """
    Bring an existing tenant table up to date with its model.

    Creating tables was never enough on its own. Django's migrations are
    recorded once, in `public`, so a migration that adds a column to a tenant
    model runs there and nowhere else - and this function used to skip any
    table that already existed. The result was a schema reporting "up to date"
    while silently missing columns, which surfaces much later as
    `column ... does not exist` on a perfectly ordinary query.

    Strictly additive: it adds columns and auto-created many-to-many tables,
    and never alters or drops anything. Repairing a genuine type change still
    needs a human.
    """
    present = _existing_columns(model._meta.db_table, schema_name)

    for field in model._meta.local_fields:
        if field.column and field.column not in present:
            editor.add_field(model, field)

    # A model that gains a many-to-many needs its join table, which is a table
    # in its own right rather than a column on this one.
    for field in model._meta.local_many_to_many:
        through = getattr(field.remote_field, 'through', None)
        if (
            through is not None
            and through._meta.auto_created
            and not _table_exists(through._meta.db_table, schema_name)
        ):
            editor.create_model(through)


def _table_exists(table_name: str, schema_name: str) -> bool:
    """Return True if *table_name* already exists in *schema_name*."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = %s
                  AND table_name   = %s
            )
            """,
            [schema_name, table_name],
        )
        return cursor.fetchone()[0]

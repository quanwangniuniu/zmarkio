"""PostgreSQL regressions for transactional tenant schema provisioning."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, connections, transaction
from psycopg2 import sql

from core.models import Organization
from core.services.tenant import provision_tenant_schema, slug_to_schema_name


pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql", reason="Requires PostgreSQL schema DDL and locks",
    ),
]


@pytest.fixture
def tenant_slugs():
    slugs = [f"provision-{uuid4().hex[:12]}-{index}" for index in range(5)]
    yield slugs
    with connection.cursor() as cursor:
        for slug in slugs:
            cursor.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(slug_to_schema_name(slug))
                )
            )


def test_five_concurrent_organization_creates_commit_complete_tenants(tenant_slugs):
    """Taking the DDL lock after INSERT deadlocks concurrent signups."""
    start = Barrier(5)

    def create_organization(slug):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout TO '45s'")
            start.wait(timeout=10)
            # Signup already has an outer transaction. The provisioning lock
            # must survive Organization.save()'s nested atomic block.
            with transaction.atomic():
                organization = Organization.objects.create(name=slug, slug=slug)
                organization.desc = "Provisioned within signup"
                organization.save(update_fields=["desc"])
            with connection.cursor() as cursor:
                cursor.execute("SHOW search_path")
                assert cursor.fetchone()[0] == "public"
            return organization.pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(create_organization, slug) for slug in tenant_slugs]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=60))
            except Exception as exc:
                outcomes.append(exc)

    assert all(isinstance(outcome, int) for outcome in outcomes), outcomes
    assert Organization.objects.filter(slug__in=tenant_slugs).count() == 5
    with connection.cursor() as cursor:
        for slug in tenant_slugs:
            # Verify the tenant FK was created, rather than merely accepting a
            # schema or organization row left behind by partial provisioning.
            cursor.execute(
                """
                SELECT count(*) FROM pg_constraint constraint_info
                JOIN pg_class tenant_table ON tenant_table.oid = constraint_info.conrelid
                JOIN pg_namespace schema_info ON schema_info.oid = tenant_table.relnamespace
                WHERE schema_info.nspname = %s AND tenant_table.relname = 'core_team'
                  AND constraint_info.contype = 'f'
                  AND constraint_info.confrelid = 'public.core_organization'::regclass
                """,
                [slug_to_schema_name(slug)],
            )
            assert cursor.fetchone()[0] == 1


@pytest.mark.parametrize("create_organization", [True, False])
def test_deferred_ddl_failure_preserves_error_and_rolls_back_schema(
    tenant_slugs, create_organization,
):
    """A failed deferred FK must not be masked by search_path cleanup SQL."""
    slug = tenant_slugs[0]

    def fail_deferred_fk(execute, statement, params, many, context):
        if str(statement).startswith("ALTER TABLE") and "REFERENCES" in str(statement):
            # Trigger a real PostgreSQL error after CREATE TABLE has succeeded,
            # during SchemaEditor.__exit__ where deferred FK DDL runs.
            return execute("SELECT 1 / 0", None, False, context)
        return execute(statement, params, many, context)

    def provision_with_failure():
        try:
            with connection.cursor() as cursor:
                cursor.execute("SHOW search_path")
                original_search_path = cursor.fetchone()[0]
            with connection.execute_wrapper(fail_deferred_fk):
                with pytest.raises(DatabaseError) as raised:
                    if create_organization:
                        Organization.objects.create(name=slug, slug=slug)
                    else:
                        # Management commands provision existing tenants directly.
                        provision_tenant_schema(slug)
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                assert cursor.fetchone()[0] == 1
                cursor.execute("SHOW search_path")
                assert cursor.fetchone()[0] == original_search_path
            return raised.value, connection.in_atomic_block
        finally:
            # Isolate a broken SchemaEditor leaking transaction state from the
            # test runner's connection, including when this regression fails.
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as executor:
        error, in_atomic_block = executor.submit(provision_with_failure).result(timeout=30)

    assert error.__cause__.pgcode == "22012"
    assert not in_atomic_block
    assert not Organization.objects.filter(slug=slug).exists()
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_namespace WHERE nspname = %s", [
            slug_to_schema_name(slug),
        ])
        assert cursor.fetchone()[0] == 0

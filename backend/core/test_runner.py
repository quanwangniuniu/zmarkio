"""
TransactionTestCase cleanup for schema-per-tenant PostgreSQL.

Creating an Organization provisions org_<slug>, whose tables hold foreign keys
into public tables (e.g. org_x.ad_copy_variation_adcopyvariation ->
public.meta_ad_creatives). Django's default TransactionTestCase teardown
flushes public with a plain TRUNCATE (allow_cascade is only True when
available_apps is set), so PostgreSQL refuses it. The failed flush leaks rows
and every later test in the class fails in setUp with IntegrityErrors.

cascade_fixture_teardown() is used by core.test_utils.TenantSafeTransactionTestCase
(for `manage.py test`) and installed on every TransactionTestCase by the root
conftest.py (for pytest).
"""
from django.core.management import call_command
from django.db import connections


def cascade_fixture_teardown(self):
    from django.contrib.contenttypes.models import ContentType

    for db_name in self._databases_names(include_mirrors=False):
        # A test may leave search_path on a tenant schema; unqualified TRUNCATE
        # names would then hit the tenant tables instead of public.
        connection = connections[db_name]
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SET search_path TO public')
        # reset_sequences stays False: RESTART IDENTITY would let new PKs
        # collide with rows other tests expect. inhibit_post_migrate stays
        # False so django_content_type and auth_permission are repopulated for
        # the TestCase tests that follow.
        call_command(
            'flush',
            verbosity=0,
            interactive=False,
            database=db_name,
            reset_sequences=False,
            allow_cascade=True,
            inhibit_post_migrate=False,
        )
    # The in-memory cache still holds pre-flush ContentType ids.
    ContentType.objects.clear_cache()

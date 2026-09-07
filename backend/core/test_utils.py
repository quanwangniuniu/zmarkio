"""
Test utilities for multi-tenant testing.

Provides base test classes that handle tenant schema context for testing
models that live in tenant schemas rather than the public schema.
"""
from django.test import TestCase, TransactionTestCase
from django.db import connection
from core.models import Organization


def grant_ai_consent(user, spreadsheet):
    """Record *user*'s one-time consent to send *spreadsheet* to an external AI.

    The agent<->spreadsheet integration gates AI analysis on
    ``spreadsheet.SpreadsheetAiConsent`` (one row per user per spreadsheet);
    tests that exercise an analysis path call this after creating the
    spreadsheet. Returns the ``SpreadsheetAiConsent`` row.
    """
    from spreadsheet.models import SpreadsheetAiConsent

    obj, _ = SpreadsheetAiConsent.objects.get_or_create(
        user=user, spreadsheet=spreadsheet
    )
    return obj


class TenantTestCase(TestCase):
    """
    TestCase that runs in a tenant schema context.

    This test case automatically:
    1. Creates a test organization
    2. Sets the database search_path to that organization's schema
    3. Runs the test
    4. Restores the search_path after the test

    Use this for testing models that live in tenant schemas (e.g., access_control,
    miro, notion_editor, etc.)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Create a test organization for this test class
        cls.test_org = Organization.objects.create(
            name=f"Test Org for {cls.__name__}",
            slug=f"test-org-{cls.__name__.lower()}",
        )
        cls.test_schema = f"org_{cls.test_org.slug.replace('-', '_')}"

    @classmethod
    def tearDownClass(cls):
        # Restore to public schema
        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public;")

        # Delete test organization (CASCADE will delete schema)
        if hasattr(cls, 'test_org'):
            cls.test_org.delete()

        super().tearDownClass()

    def setUp(self):
        super().setUp()

        # Mirror the TenantSchemaMiddleware: tenant schema first, public as fallback.
        # Including public allows models that live in the public schema (e.g.
        # AdminAuditEvent) to be written/read even during tenant-scoped tests.
        with connection.cursor() as cursor:
            cursor.execute(f"SET search_path TO {self.test_schema}, public;")

    def tearDown(self):
        # Don't explicitly restore schema here - Django's transaction rollback
        # will reset the connection state. Just call parent tearDown.
        super().tearDown()

    def create_user(self, **kwargs):
        """
        Helper method to create a user in the public schema.

        Since CustomUser lives in public schema but tests run in tenant schema,
        this helper temporarily switches to public to create the user, then
        switches back to tenant schema.
        """
        from django.contrib.auth import get_user_model

        User = get_user_model()

        # Temporarily switch to public schema
        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public;")

        try:
            user = User.objects.create_user(**kwargs)
        finally:
            # Switch back to tenant schema (with public fallback)
            with connection.cursor() as cursor:
                cursor.execute(f"SET search_path TO {self.test_schema}, public;")

        return user


class TenantTransactionTestCase(TransactionTestCase):
    """
    TransactionTestCase that runs in a tenant schema context.

    Similar to TenantTestCase but uses TransactionTestCase as the base,
    which is needed for tests that require transaction control or test
    database flushing.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Create a test organization for this test class
        cls.test_org = Organization.objects.create(
            name=f"Test Org for {cls.__name__}",
            slug=f"test-org-{cls.__name__.lower()}",
        )
        cls.test_schema = f"org_{cls.test_org.slug.replace('-', '_')}"

    @classmethod
    def tearDownClass(cls):
        # Restore to public schema
        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public;")

        # Delete test organization
        if hasattr(cls, 'test_org'):
            cls.test_org.delete()

        super().tearDownClass()

    def setUp(self):
        super().setUp()

        # Mirror the TenantSchemaMiddleware: tenant schema first, public as fallback.
        with connection.cursor() as cursor:
            cursor.execute(f"SET search_path TO {self.test_schema}, public;")

    def tearDown(self):
        # Don't explicitly restore schema here - Django's transaction rollback
        # will reset the connection state. Just call parent tearDown.
        super().tearDown()

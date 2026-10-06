import os
import statistics
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import RequestFactory, TestCase
from django.utils import timezone

from access_control.middleware.authorization import AuthorizationMiddleware
from access_control.models import RolePermission, UserRole
from access_control.services import (
    get_user_permission_bundle,
    invalidate_user_permission_cache,
    permission_cache_key,
)
from core.models import Organization, Permission, Role
from core.services.tenant import slug_to_schema_name


class PermissionCacheTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="PermissionCacheOrg")
        cls.schema = slug_to_schema_name(cls.org.slug)

        with connection.cursor() as cursor:
            cursor.execute(f"SET search_path TO {cls.schema}, public")

        cls.asset_view, _ = Permission.objects.get_or_create(
            module="ASSET",
            action="VIEW",
        )
        cls.campaign_edit, _ = Permission.objects.get_or_create(
            module="CAMPAIGN",
            action="EDIT",
        )

        cls.viewer_role = Role.objects.create(
            organization=cls.org,
            name="CacheViewer",
            level=10,
        )
        RolePermission.objects.create(
            role=cls.viewer_role,
            permission=cls.asset_view,
        )

        cls.editor_role = Role.objects.create(
            organization=cls.org,
            name="CacheEditor",
            level=10,
        )
        RolePermission.objects.create(
            role=cls.editor_role,
            permission=cls.campaign_edit,
        )

        User = get_user_model()
        cls.user = User.objects.create_user(
            username="cache-user",
            email="cache-user@example.com",
            password="pw",
        )

        cls.viewer_user_role = UserRole.objects.create(
            user=cls.user,
            role=cls.viewer_role,
            valid_from=timezone.now(),
        )

        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public")

    def setUp(self):
        with connection.cursor() as cursor:
            cursor.execute(f"SET search_path TO {self.schema}, public")

        invalidate_user_permission_cache(
            self.schema,
            self.user.id,
        )

    def tearDown(self):
        invalidate_user_permission_cache(
            self.schema,
            self.user.id,
        )

        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public")

        super().tearDown()

    def _cache_key(self):
        return permission_cache_key(
            self.schema,
            self.user.id,
        )

    def _warm_cache(self):
        with self.captureOnCommitCallbacks(execute=True):
            bundle = get_user_permission_bundle(
                self.user.id,
                self.schema,
            )
        return bundle

    def test_cache_miss_populates_bundle_and_cache_hit_avoids_db_queries(self):
        key = self._cache_key()

        self.assertIsNone(cache.get(key))

        bundle = self._warm_cache()

        self.assertIn(
            "ASSET:VIEW",
            bundle["permissions"],
        )
        self.assertEqual(
            cache.get(key),
            bundle,
        )

        with self.assertNumQueries(0):
            cached_bundle = get_user_permission_bundle(
                self.user.id,
                self.schema,
            )

        self.assertEqual(
            cached_bundle,
            bundle,
        )

    def test_user_role_change_invalidates_cached_bundle(self):
        key = self._cache_key()

        self._warm_cache()
        self.assertIsNotNone(cache.get(key))

        with self.captureOnCommitCallbacks(execute=True):
            UserRole.objects.create(
                user=self.user,
                role=self.editor_role,
                valid_from=timezone.now(),
            )

        self.assertIsNone(cache.get(key))

        refreshed = self._warm_cache()

        self.assertIn(
            "CAMPAIGN:EDIT",
            refreshed["permissions"],
        )

    def test_role_permission_change_invalidates_cached_bundle(self):
        key = self._cache_key()

        self._warm_cache()
        self.assertIsNotNone(cache.get(key))

        with self.captureOnCommitCallbacks(execute=True):
            RolePermission.objects.create(
                role=self.viewer_role,
                permission=self.campaign_edit,
            )

        self.assertIsNone(cache.get(key))

        refreshed = self._warm_cache()

        self.assertIn(
            "CAMPAIGN:EDIT",
            refreshed["permissions"],
        )

    def test_role_change_invalidates_cached_bundle(self):
        key = self._cache_key()

        self._warm_cache()
        self.assertIsNotNone(cache.get(key))

        with self.captureOnCommitCallbacks(execute=True):
            self.viewer_role.name = "CacheViewerUpdated"
            self.viewer_role.save(update_fields=["name"])

        self.assertIsNone(cache.get(key))

    def test_user_role_delete_invalidates_cached_bundle(self):
        key = self._cache_key()

        self._warm_cache()
        self.assertIsNotNone(cache.get(key))

        with self.captureOnCommitCallbacks(execute=True):
            self.viewer_user_role.delete()

        self.assertIsNone(cache.get(key))

        refreshed = self._warm_cache()

        self.assertNotIn(
            "ASSET:VIEW",
            refreshed["permissions"],
        )

    def test_role_permission_delete_invalidates_cached_bundle(self):
        key = self._cache_key()

        self._warm_cache()
        self.assertIsNotNone(cache.get(key))

        role_permission = RolePermission.objects.get(
            role=self.viewer_role,
            permission=self.asset_view,
        )

        with self.captureOnCommitCallbacks(execute=True):
            role_permission.delete()

        self.assertIsNone(cache.get(key))

        refreshed = self._warm_cache()

        self.assertNotIn(
            "ASSET:VIEW",
            refreshed["permissions"],
        )

    def test_user_role_change_reflected_within_one_second(self):
        self._warm_cache()

        start = time.perf_counter()

        with self.captureOnCommitCallbacks(execute=True):
            UserRole.objects.create(
                user=self.user,
                role=self.editor_role,
                valid_from=timezone.now(),
            )

        refreshed = self._warm_cache()
        elapsed = time.perf_counter() - start

        self.assertIn(
            "CAMPAIGN:EDIT",
            refreshed["permissions"],
        )
        self.assertLess(
            elapsed,
            1.0,
        )

    def test_cache_failure_falls_back_to_postgresql_resolution(self):
        with patch(
            "access_control.services.cache.get",
            side_effect=RuntimeError("cache unavailable"),
        ), patch(
            "access_control.services.cache.set",
            side_effect=RuntimeError("cache unavailable"),
        ):
            bundle = get_user_permission_bundle(
                self.user.id,
                self.schema,
            )

        self.assertIn(
            "ASSET:VIEW",
            bundle["permissions"],
        )

    def test_warmed_cache_reduces_p95_request_latency(self):
        if os.environ.get("MED299_BENCHMARK") != "1":
            self.skipTest(
                "Set MED299_BENCHMARK=1 to run the p95 benchmark."
            )

        factory = RequestFactory()
        middleware = AuthorizationMiddleware()

        def run_request():
            request = factory.get("/api/assets/list/")
            request.user = self.user

            with self.captureOnCommitCallbacks(execute=True):
                start = time.perf_counter()

                response = middleware.process_view(
                    request,
                    lambda request: None,
                    (),
                    {},
                )

                elapsed = time.perf_counter() - start

            self.assertIsNone(response)
            return elapsed

        cold_times = []

        for _ in range(100):
            invalidate_user_permission_cache(
                self.schema,
                self.user.id,
            )
            cold_times.append(run_request())

        invalidate_user_permission_cache(
            self.schema,
            self.user.id,
        )
        run_request()

        warm_times = [
            run_request()
            for _ in range(100)
        ]

        cold_p95 = statistics.quantiles(
            cold_times,
            n=100,
            method="inclusive",
        )[94]

        warm_p95 = statistics.quantiles(
            warm_times,
            n=100,
            method="inclusive",
        )[94]

        self.assertLess(
            warm_p95,
            cold_p95,
        )
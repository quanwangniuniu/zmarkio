"""
API-level tests for session management endpoints:
  GET  /auth/sessions/         — list active sessions
  DELETE /auth/sessions/<jti>/ — revoke a session

Redis is mocked so no real Redis server is required.
"""
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model

User = get_user_model()

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "session-api-tests",
    }
}


def make_mock_redis(registry: dict):
    """
    Build a MagicMock that simulates sorted-set operations using a plain dict
    {jti_bytes: score}.  `registry` is shared so callers can inspect state.
    """
    mock = MagicMock()

    def zadd(key, mapping):
        for member, score in mapping.items():
            b = member.encode() if isinstance(member, str) else member
            registry[b] = score

    def zcard(key):
        return len(registry)

    def zpopmin(key, count=1):
        ordered = sorted(registry.items(), key=lambda x: x[1])
        popped = ordered[:count]
        for member, _ in popped:
            del registry[member]
        return popped

    def zrange(key, start, end):
        ordered = sorted(registry.items(), key=lambda x: x[1])
        if end == -1:
            return [m for m, _ in ordered[start:]]
        return [m for m, _ in ordered[start : end + 1]]

    def zrem(key, member):
        b = member.encode() if isinstance(member, str) else member
        registry.pop(b, None)

    mock.zadd.side_effect = zadd
    mock.zcard.side_effect = zcard
    mock.zpopmin.side_effect = zpopmin
    mock.zrange.side_effect = zrange
    mock.zrem.side_effect = zrem
    mock.expire.return_value = True
    return mock


@override_settings(CACHES=TEST_CACHES)
class SessionListViewTests(APITestCase):

    def setUp(self):
        cache.clear()
        self.registry: dict = {}
        self.mock_redis = make_mock_redis(self.registry)

        self.redis_patcher = patch(
            "authentication.session_registry.get_redis_connection",
            return_value=self.mock_redis,
        )
        self.redis_patcher.start()

        self.user = User.objects.create_user(
            email="sessions@example.com",
            password="testpass123",
            username="sessionsuser",
            is_verified=True,
            is_active=True,
        )
        self.sessions_url = reverse("session-list")

    def tearDown(self):
        self.redis_patcher.stop()
        cache.clear()

    def _auth_header(self):
        """Log in and return Authorization header using the access token."""
        resp = self.client.post(
            reverse("login"),
            {"email": "sessions@example.com", "password": "testpass123"},
        )
        token = resp.data["token"]
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def test_unauthenticated_request_returns_401(self):
        resp = self.client.get(self.sessions_url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_gets_session_list(self):
        headers = self._auth_header()
        resp = self.client.get(self.sessions_url, **headers)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsInstance(resp.data, list)

    def test_session_list_contains_jti(self):
        headers = self._auth_header()
        resp = self.client.get(self.sessions_url, **headers)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(len(resp.data) >= 1)
        self.assertIn("jti", resp.data[0])

    def test_session_list_contains_meta_fields(self):
        headers = self._auth_header()
        resp = self.client.get(self.sessions_url, **headers)
        session = resp.data[0]
        self.assertIn("ip", session)
        self.assertIn("user_agent", session)
        self.assertIn("created_at", session)


@override_settings(CACHES=TEST_CACHES)
class SessionRevokeViewTests(APITestCase):

    def setUp(self):
        cache.clear()
        self.registry: dict = {}
        self.mock_redis = make_mock_redis(self.registry)

        self.redis_patcher = patch(
            "authentication.session_registry.get_redis_connection",
            return_value=self.mock_redis,
        )
        self.redis_patcher.start()

        self.user = User.objects.create_user(
            email="revoke@example.com",
            password="testpass123",
            username="revokeuser",
            is_verified=True,
            is_active=True,
        )

    def tearDown(self):
        self.redis_patcher.stop()
        cache.clear()

    def _login(self):
        resp = self.client.post(
            reverse("login"),
            {"email": "revoke@example.com", "password": "testpass123"},
        )
        return resp.data["token"], resp.data["refresh"]

    def _auth_header(self, token):
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def test_revoke_existing_session_returns_200(self):
        token, _ = self._login()
        headers = self._auth_header(token)

        # Get session jti from list
        sessions_resp = self.client.get(reverse("session-list"), **headers)
        jti = sessions_resp.data[0]["jti"]

        revoke_url = reverse("session-revoke", kwargs={"jti": jti})
        resp = self.client.delete(revoke_url, **headers)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_revoke_removes_session_from_list(self):
        # Login twice so we have two sessions
        token1, _ = self._login()
        token2, _ = self._login()

        # Use token2 to get the session list and find token1's jti
        headers2 = self._auth_header(token2)
        sessions_resp = self.client.get(reverse("session-list"), **headers2)
        self.assertEqual(len(sessions_resp.data), 2)

        # Revoke the first session (oldest jti) using token2
        jti_to_revoke = sessions_resp.data[0]["jti"]
        self.client.delete(
            reverse("session-revoke", kwargs={"jti": jti_to_revoke}), **headers2
        )

        # Confirm it's no longer in the list
        sessions_after = self.client.get(reverse("session-list"), **headers2)
        jtis_after = [s["jti"] for s in sessions_after.data]
        self.assertNotIn(jti_to_revoke, jtis_after)

    def test_revoke_adds_jti_to_blacklist(self):
        from authentication.session_registry import SessionRegistry

        token, _ = self._login()
        headers = self._auth_header(token)

        sessions_resp = self.client.get(reverse("session-list"), **headers)
        jti = sessions_resp.data[0]["jti"]

        self.client.delete(
            reverse("session-revoke", kwargs={"jti": jti}), **headers
        )

        self.assertTrue(SessionRegistry.is_evicted(jti))

    def test_unauthenticated_revoke_returns_401(self):
        resp = self.client.delete(
            reverse("session-revoke", kwargs={"jti": "some-jti"})
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


@override_settings(CACHES=TEST_CACHES)
class SessionCapEvictionTests(APITestCase):
    """
    Tests that exceeding max_concurrent_sessions evicts the oldest session.
    """

    def setUp(self):
        cache.clear()
        self.registry: dict = {}
        self.mock_redis = make_mock_redis(self.registry)

        self.redis_patcher = patch(
            "authentication.session_registry.get_redis_connection",
            return_value=self.mock_redis,
        )
        self.redis_patcher.start()

        self.user = User.objects.create_user(
            email="cap@example.com",
            password="testpass123",
            username="capuser",
            is_verified=True,
            is_active=True,
        )

    def tearDown(self):
        self.redis_patcher.stop()
        cache.clear()

    def _login(self):
        resp = self.client.post(
            reverse("login"),
            {"email": "cap@example.com", "password": "testpass123"},
        )
        return resp.data["token"]

    def test_sessions_do_not_exceed_cap(self):
        """After 6 logins with cap=5, only 5 sessions should remain."""
        from core.models import Organization
        from authentication.session_registry import SessionRegistry

        # Give the user an org with cap=5
        org = Organization.objects.create(name="Test Org", max_concurrent_sessions=5)
        self.user.current_organization = org
        self.user.save()

        for _ in range(6):
            self._login()

        sessions = SessionRegistry.list_sessions(self.user.pk)
        self.assertLessEqual(len(sessions), 5)

    def test_oldest_session_evicted_when_cap_exceeded(self):
        """The oldest session JTI should end up blacklisted after overflow."""
        from core.models import Organization
        from authentication.session_registry import SessionRegistry

        org = Organization.objects.create(name="Cap Org", max_concurrent_sessions=2)
        self.user.current_organization = org
        self.user.save()

        # First login — this JTI should be evicted when cap=2 is exceeded
        first_token = self._login()
        first_sessions = SessionRegistry.list_sessions(self.user.pk)
        self.assertEqual(len(first_sessions), 1)
        first_jti = first_sessions[0]["jti"]

        # Second login — fills the cap
        self._login()
        # Third login — pushes over cap, oldest (first) should be evicted
        self._login()

        self.assertTrue(SessionRegistry.is_evicted(first_jti))


@override_settings(CACHES=TEST_CACHES)
class SessionEvictionEnforcementTests(APITestCase):
    """Verify that a revoked session token is rejected on subsequent requests."""

    def setUp(self):
        cache.clear()
        self.registry: dict = {}
        self.mock_redis = make_mock_redis(self.registry)
        self.redis_patcher = patch(
            "authentication.session_registry.get_redis_connection",
            return_value=self.mock_redis,
        )
        self.redis_patcher.start()

        self.user = User.objects.create_user(
            email="evict@example.com",
            password="testpass123",
            username="evictuser",
            is_verified=True,
            is_active=True,
        )

    def tearDown(self):
        self.redis_patcher.stop()
        cache.clear()

    def _login(self):
        resp = self.client.post(
            reverse("login"),
            {"email": "evict@example.com", "password": "testpass123"},
        )
        return resp.data["token"]

    def test_evicted_token_returns_401_on_next_request(self):
        token = self._login()
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}

        sessions_resp = self.client.get(reverse("session-list"), **headers)
        jti = sessions_resp.data[0]["jti"]

        # Revoke the session
        self.client.delete(
            reverse("session-revoke", kwargs={"jti": jti}), **headers
        )

        # The same token must now be rejected
        resp = self.client.get(reverse("session-list"), **headers)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_revoked_token_still_works_after_another_session_revoked(self):
        """Revoking one session must not affect other sessions."""
        token1 = self._login()
        token2 = self._login()
        headers1 = {"HTTP_AUTHORIZATION": f"Bearer {token1}"}
        headers2 = {"HTTP_AUTHORIZATION": f"Bearer {token2}"}

        # Get sessions using token2, find token1's jti (oldest)
        sessions_resp = self.client.get(reverse("session-list"), **headers2)
        jti_to_revoke = sessions_resp.data[0]["jti"]

        self.client.delete(
            reverse("session-revoke", kwargs={"jti": jti_to_revoke}), **headers2
        )

        # token2 should still work
        resp = self.client.get(reverse("session-list"), **headers2)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


@override_settings(CACHES=TEST_CACHES)
class SessionLogoutTests(APITestCase):
    """Verify that logout removes the session from the registry."""

    def setUp(self):
        cache.clear()
        self.registry: dict = {}
        self.mock_redis = make_mock_redis(self.registry)
        self.redis_patcher = patch(
            "authentication.session_registry.get_redis_connection",
            return_value=self.mock_redis,
        )
        self.redis_patcher.start()

        self.user = User.objects.create_user(
            email="logout@example.com",
            password="testpass123",
            username="logoutuser",
            is_verified=True,
            is_active=True,
        )

    def tearDown(self):
        self.redis_patcher.stop()
        cache.clear()

    def test_logout_removes_session_from_registry(self):
        from authentication.session_registry import SessionRegistry

        resp = self.client.post(
            reverse("login"),
            {"email": "logout@example.com", "password": "testpass123"},
        )
        token = resp.data["token"]
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}

        self.client.post(reverse("logout"), **headers)

        sessions = SessionRegistry.list_sessions(self.user.pk)
        self.assertEqual(sessions, [])

    def test_logout_does_not_blacklist_token(self):
        """Normal logout should not block the token — only eviction does."""
        from authentication.session_registry import SessionRegistry

        resp = self.client.post(
            reverse("login"),
            {"email": "logout@example.com", "password": "testpass123"},
        )
        token = resp.data["token"]
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}

        sessions_before = self.client.get(reverse("session-list"), **headers)
        jti = sessions_before.data[0]["jti"]

        self.client.post(reverse("logout"), **headers)

        self.assertFalse(SessionRegistry.is_evicted(jti))


@override_settings(CACHES=TEST_CACHES)
class AuthTokensTests(APITestCase):
    """Verify that refresh_jti is correctly embedded in refresh and access tokens."""

    def setUp(self):
        cache.clear()
        self.registry: dict = {}
        self.mock_redis = make_mock_redis(self.registry)
        self.redis_patcher = patch(
            "authentication.session_registry.get_redis_connection",
            return_value=self.mock_redis,
        )
        self.redis_patcher.start()

        self.user = User.objects.create_user(
            email="tokenparity@example.com",
            password="testpass123",
            username="tokenparityuser",
            is_verified=True,
            is_active=True,
        )

    def tearDown(self):
        self.redis_patcher.stop()
        cache.clear()

    def test_refresh_token_contains_refresh_jti_equal_to_its_own_jti(self):
        from core.services.auth_tokens import build_user_refresh_token

        refresh = build_user_refresh_token(self.user)
        self.assertEqual(refresh["refresh_jti"], str(refresh["jti"]))

    def test_access_token_inherits_refresh_jti_from_refresh_token(self):
        from core.services.auth_tokens import build_user_refresh_token

        refresh = build_user_refresh_token(self.user)
        access = refresh.access_token
        self.assertEqual(access["refresh_jti"], str(refresh["jti"]))

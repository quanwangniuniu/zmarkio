"""
Unit tests for SessionRegistry.

Redis sorted-set operations are replaced with a lightweight in-process fake so
the tests run without a real Redis server.  Django cache is swapped to locmem.
"""
import time
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from authentication.session_registry import SessionRegistry

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "session-registry-tests",
    }
}


class FakeSortedSet:
    """Minimal sorted-set backed by a plain dict {member: score}."""

    def __init__(self):
        self._data: dict[bytes, float] = {}

    def zadd(self, key, mapping):
        for member, score in mapping.items():
            self._data[member.encode() if isinstance(member, str) else member] = score

    def zcard(self, key):
        return len(self._data)

    def zpopmin(self, key, count=1):
        ordered = sorted(self._data.items(), key=lambda x: x[1])
        popped = ordered[:count]
        for member, _ in popped:
            del self._data[member]
        return popped

    def zrange(self, key, start, end):
        ordered = sorted(self._data.items(), key=lambda x: x[1])
        if end == -1:
            return [m for m, _ in ordered[start:]]
        return [m for m, _ in ordered[start : end + 1]]

    def zrem(self, key, member):
        key_bytes = member.encode() if isinstance(member, str) else member
        self._data.pop(key_bytes, None)

    def expire(self, key, ttl):
        pass


def make_fake_redis():
    """Return a MagicMock whose sorted-set methods delegate to FakeSortedSet."""
    fake = FakeSortedSet()
    mock = MagicMock()
    mock.zadd.side_effect = fake.zadd
    mock.zcard.side_effect = fake.zcard
    mock.zpopmin.side_effect = fake.zpopmin
    mock.zrange.side_effect = fake.zrange
    mock.zrem.side_effect = fake.zrem
    mock.expire.side_effect = fake.expire
    return mock


@override_settings(CACHES=TEST_CACHES)
class TestRegisterSession(TestCase):

    def setUp(self):
        cache.clear()
        self.redis = make_fake_redis()
        self.patcher = patch(
            "authentication.session_registry.get_redis_connection",
            return_value=self.redis,
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        cache.clear()

    def test_register_stores_jti_in_sorted_set(self):
        evicted = SessionRegistry.register_session(1, "jti-a", {}, cap=5)
        self.assertEqual(evicted, [])
        self.redis.zadd.assert_called_once()

    def test_register_stores_meta_in_cache(self):
        meta = {"ip": "1.2.3.4", "user_agent": "Chrome"}
        SessionRegistry.register_session(1, "jti-b", meta, cap=5)
        stored = cache.get("session:meta:jti-b")
        self.assertEqual(stored, meta)

    def test_no_eviction_under_cap(self):
        for i in range(3):
            evicted = SessionRegistry.register_session(1, f"jti-{i}", {}, cap=5)
            self.assertEqual(evicted, [])

    def test_eviction_when_cap_exceeded(self):
        # Fill to cap
        for i in range(5):
            SessionRegistry.register_session(1, f"jti-{i}", {}, cap=5)
        # 6th login should evict the oldest
        evicted = SessionRegistry.register_session(1, "jti-new", {}, cap=5)
        self.assertEqual(len(evicted), 1)
        self.assertEqual(evicted[0], "jti-0")

    def test_eviction_returns_multiple_when_far_over_cap(self):
        # Register 7 sessions with cap=5 (2 excess)
        for i in range(7):
            SessionRegistry.register_session(1, f"jti-{i}", {}, cap=5)
        # After all registrations the sorted set should hold exactly 5
        self.assertEqual(self.redis.zcard(None), 5)


@override_settings(CACHES=TEST_CACHES)
class TestEvictSession(TestCase):

    def setUp(self):
        cache.clear()
        self.redis = make_fake_redis()
        self.patcher = patch(
            "authentication.session_registry.get_redis_connection",
            return_value=self.redis,
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        cache.clear()

    def test_evicted_jti_added_to_blacklist(self):
        SessionRegistry.evict_session(1, "jti-x")
        self.assertTrue(SessionRegistry.is_evicted("jti-x"))

    def test_non_evicted_jti_not_in_blacklist(self):
        self.assertFalse(SessionRegistry.is_evicted("jti-unknown"))

    def test_evict_removes_from_sorted_set(self):
        SessionRegistry.register_session(1, "jti-y", {}, cap=5)
        SessionRegistry.evict_session(1, "jti-y")
        sessions = SessionRegistry.list_sessions(1)
        self.assertEqual(sessions, [])

    def test_evict_removes_meta_from_cache(self):
        meta = {"ip": "9.9.9.9"}
        SessionRegistry.register_session(1, "jti-z", meta, cap=5)
        SessionRegistry.evict_session(1, "jti-z")
        self.assertIsNone(cache.get("session:meta:jti-z"))


@override_settings(CACHES=TEST_CACHES)
class TestListSessions(TestCase):

    def setUp(self):
        cache.clear()
        self.redis = make_fake_redis()
        self.patcher = patch(
            "authentication.session_registry.get_redis_connection",
            return_value=self.redis,
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        cache.clear()

    def test_list_returns_registered_sessions_with_meta(self):
        SessionRegistry.register_session(1, "jti-1", {"ip": "1.1.1.1"}, cap=5)
        SessionRegistry.register_session(1, "jti-2", {"ip": "2.2.2.2"}, cap=5)
        sessions = SessionRegistry.list_sessions(1)
        jtis = [s["jti"] for s in sessions]
        self.assertIn("jti-1", jtis)
        self.assertIn("jti-2", jtis)

    def test_list_returns_empty_for_unknown_user(self):
        sessions = SessionRegistry.list_sessions(999)
        self.assertEqual(sessions, [])

    def test_list_includes_meta_fields(self):
        meta = {"ip": "5.5.5.5", "user_agent": "Firefox"}
        SessionRegistry.register_session(1, "jti-meta", meta, cap=5)
        sessions = SessionRegistry.list_sessions(1)
        self.assertEqual(sessions[0]["ip"], "5.5.5.5")
        self.assertEqual(sessions[0]["user_agent"], "Firefox")


@override_settings(CACHES=TEST_CACHES)
class TestRemoveSession(TestCase):
    """remove_session removes from registry without blacklisting (normal logout path)."""

    def setUp(self):
        cache.clear()
        self.redis = make_fake_redis()
        self.patcher = patch(
            "authentication.session_registry.get_redis_connection",
            return_value=self.redis,
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        cache.clear()

    def test_remove_session_deletes_from_registry(self):
        SessionRegistry.register_session(1, "jti-rm", {}, cap=5)
        SessionRegistry.remove_session(1, "jti-rm")
        self.assertEqual(SessionRegistry.list_sessions(1), [])

    def test_remove_session_does_not_add_to_blacklist(self):
        SessionRegistry.register_session(1, "jti-rm2", {}, cap=5)
        SessionRegistry.remove_session(1, "jti-rm2")
        self.assertFalse(SessionRegistry.is_evicted("jti-rm2"))

    def test_remove_session_clears_meta(self):
        meta = {"ip": "1.1.1.1"}
        SessionRegistry.register_session(1, "jti-rm3", meta, cap=5)
        SessionRegistry.remove_session(1, "jti-rm3")
        self.assertIsNone(cache.get("session:meta:jti-rm3"))


@override_settings(CACHES=TEST_CACHES)
class TestDeleteSession(TestCase):
    """delete_session evicts the session — adds to blacklist and removes from registry."""

    def setUp(self):
        cache.clear()
        self.redis = make_fake_redis()
        self.patcher = patch(
            "authentication.session_registry.get_redis_connection",
            return_value=self.redis,
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        cache.clear()

    def test_delete_session_adds_to_blacklist(self):
        SessionRegistry.register_session(1, "jti-del", {}, cap=5)
        SessionRegistry.delete_session(1, "jti-del")
        self.assertTrue(SessionRegistry.is_evicted("jti-del"))

    def test_delete_session_removes_from_registry(self):
        SessionRegistry.register_session(1, "jti-del2", {}, cap=5)
        SessionRegistry.delete_session(1, "jti-del2")
        self.assertEqual(SessionRegistry.list_sessions(1), [])

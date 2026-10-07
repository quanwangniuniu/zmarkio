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
    """Minimal Redis fake covering sorted-set, key-value, and hash operations."""

    def __init__(self):
        self._data: dict[bytes, float] = {}
        self._kv: dict[str, object] = {}
        self._hashes: dict[str, dict] = {}

    # --- sorted set ---
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

    # --- key-value (blacklist) ---
    def set(self, key, value, ex=None):
        self._kv[key] = value

    def exists(self, key):
        return 1 if key in self._kv or key in self._hashes else 0

    def delete(self, *keys):
        for k in keys:
            self._kv.pop(k, None)
            self._hashes.pop(k, None)

    # --- hash (metadata) ---
    def hset(self, key, mapping=None):
        if mapping:
            self._hashes[key] = {k: v for k, v in mapping.items()}

    def hgetall(self, key):
        raw = self._hashes.get(key, {})
        return {
            (k.encode() if isinstance(k, str) else k): (v.encode() if isinstance(v, str) else v)
            for k, v in raw.items()
        }

    # --- eval (simulate REGISTER_AND_EVICT_LUA) ---
    def eval(self, script, numkeys, *args):
        register_key = args[0]
        jti = args[1]
        score = float(args[2])
        cap = int(args[3])
        ttl = int(args[4])
        blacklist_prefix = args[5]
        meta_prefix = args[6]

        self.zadd(register_key, {jti: score})
        self.expire(register_key, ttl)

        count = self.zcard(register_key)
        excess = count - cap

        evicted = []
        if excess > 0:
            oldest = self.zpopmin(register_key, excess)
            for member, _ in oldest:
                evicted_jti = member.decode() if isinstance(member, bytes) else member
                self.set(blacklist_prefix + evicted_jti, 1, ex=ttl)
                self.delete(meta_prefix + evicted_jti)
                evicted.append(evicted_jti.encode() if isinstance(evicted_jti, str) else evicted_jti)

        return evicted


def make_fake_redis():
    """Return a MagicMock whose Redis methods delegate to FakeSortedSet."""
    fake = FakeSortedSet()
    mock = MagicMock()
    mock.zadd.side_effect = fake.zadd
    mock.zcard.side_effect = fake.zcard
    mock.zpopmin.side_effect = fake.zpopmin
    mock.zrange.side_effect = fake.zrange
    mock.zrem.side_effect = fake.zrem
    mock.expire.side_effect = fake.expire
    mock.set.side_effect = fake.set
    mock.exists.side_effect = fake.exists
    mock.delete.side_effect = fake.delete
    mock.hset.side_effect = fake.hset
    mock.hgetall.side_effect = fake.hgetall
    mock.eval.side_effect = fake.eval
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
        self.redis.eval.assert_called_once()

    def test_register_stores_meta_in_redis_hash(self):
        meta = {"ip": "1.2.3.4", "user_agent": "Chrome"}
        SessionRegistry.register_session(1, "jti-b", meta, cap=5)
        self.redis.hset.assert_called_once_with(
            "session:meta:jti-b",
            mapping={"ip": "1.2.3.4", "user_agent": "Chrome", "created_at": ""},
        )

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

    def test_evict_removes_meta_from_redis(self):
        meta = {"ip": "9.9.9.9"}
        SessionRegistry.register_session(1, "jti-z", meta, cap=5)
        SessionRegistry.evict_session(1, "jti-z")
        self.redis.delete.assert_called_with("session:meta:jti-z")

    def test_evict_sends_websocket_revocation(self):
        mock_sync_send = MagicMock()
        mock_async_to_sync = MagicMock(return_value=mock_sync_send)
        mock_channel_layer = MagicMock()

        with patch("channels.layers.get_channel_layer", return_value=mock_channel_layer):
            with patch("asgiref.sync.async_to_sync", mock_async_to_sync):
                SessionRegistry.evict_session(42, "jti-ws")

        mock_async_to_sync.assert_called_once_with(mock_channel_layer.group_send)
        mock_sync_send.assert_called_once_with(
            "chat_user_42",
            {"type": "user_session_revoked", "reason": "session_evicted"},
        )

    def test_evict_skips_websocket_when_no_channel_layer(self):
        with patch("channels.layers.get_channel_layer", return_value=None):
            # Should not raise even without a channel layer
            SessionRegistry.evict_session(1, "jti-no-ws")
        self.assertTrue(SessionRegistry.is_evicted("jti-no-ws"))


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
        self.redis.delete.assert_called_with("session:meta:jti-rm3")


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

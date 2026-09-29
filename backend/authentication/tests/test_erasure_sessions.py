from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from authentication.session_registry import (
    REGISTER_KEY,
    REVOKE_ALL_LUA,
    SessionRegistry,
    TOKEN_TTL,
)


class ErasureSessionTests(SimpleTestCase):
    @patch("authentication.session_registry.get_redis_connection")
    def test_revoke_all_sessions_uses_one_atomic_redis_operation(self, get_connection):
        redis = Mock()
        redis.eval.return_value = 2
        get_connection.return_value = redis

        self.assertEqual(SessionRegistry.revoke_all_sessions(42), 2)
        redis.eval.assert_called_once_with(
            REVOKE_ALL_LUA, 1, REGISTER_KEY.format(user_id=42),
            "session:blacklist:", "session:meta:", TOKEN_TTL,
        )
        self.assertIn("redis.call('DEL', ARGV[2] .. jti)", REVOKE_ALL_LUA)
        self.assertIn("redis.call('DEL', KEYS[1])", REVOKE_ALL_LUA)

import time
from typing import cast
from redis import Redis
from django.core.cache import cache
from django_redis import get_redis_connection

TOKEN_TTL = 60 * 60 * 24 * 4 # 4 days, same as JWT refresh token lifetime

REGISTER_KEY = "session:registry:{user_id}"
META_KEY = "session:meta:{jti}"
BLACKLIST_KEY = "session:blacklist:{jti}"

class SessionRegistry:

    @staticmethod
    def register_session(user_id, jti, meta: dict, cap: int) -> list[str]:
        redis = cast(Redis, get_redis_connection("default"))
        register_key = REGISTER_KEY.format(user_id = user_id)
        meta_key = META_KEY.format(jti = jti)

        # Add new session to sorted set, score = current timestamp
        redis.zadd(register_key, {jti: time.time()})
        redis.expire(register_key, TOKEN_TTL)

        # Store session metadata
        cache.set(meta_key, meta, timeout=TOKEN_TTL)

        # Check if over cap, evict oldest
        evicted = []
        excess = redis.zcard(register_key) - cap
        if excess > 0:
            oldest = redis.zpopmin(register_key, count=excess)
            evicted = [item[0].decode() for item in oldest]

        return evicted

    @staticmethod
    def evict_session(user_id, jti):
        blacklist_key = BLACKLIST_KEY.format(jti=jti)
        register_key = REGISTER_KEY.format(user_id=user_id)
        redis = cast(Redis, get_redis_connection("default"))

        # Add to blacklist
        cache.set(blacklist_key, True, timeout=TOKEN_TTL)

        # Remove from registry
        redis.zrem(register_key, jti)

        # Delete metadata
        meta_key = META_KEY.format(jti=jti)
        cache.delete(meta_key)

    @staticmethod
    def is_evicted(jti) -> bool:
        blacklist_key = BLACKLIST_KEY.format(jti=jti)
        return cache.get(blacklist_key) is not None

    @staticmethod
    def list_sessions(user_id) -> list[dict]:
        redis = cast(Redis, get_redis_connection("default"))
        register_key = REGISTER_KEY.format(user_id=user_id)

        jtis = redis.zrange(register_key, 0, -1)

        sessions = []
        for jti_bytes in jtis:
            jti = jti_bytes.decode()
            meta_key = META_KEY.format(jti=jti)
            meta = cache.get(meta_key) or {}
            sessions.append({"jti": jti, **meta})

        return sessions

    @staticmethod
    def remove_session(user_id, jti):
        """Remove a session from the registry without blacklisting it (for normal logout)."""
        register_key = REGISTER_KEY.format(user_id=user_id)
        redis = cast(Redis, get_redis_connection("default"))
        removed = redis.zrem(register_key, jti)
        meta_key = META_KEY.format(jti=jti)
        cache.delete(meta_key)
        return removed

    @staticmethod
    def delete_session(user_id, jti):
        SessionRegistry.evict_session(user_id, jti)
import time
from typing import cast
from redis import Redis
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
        redis.hset(meta_key, mapping={
            "ip": meta.get("ip", ""),
            "user_agent": meta.get("user_agent", ""),
            "created_at": meta.get("created_at", "")
        })
        redis.expire(meta_key, TOKEN_TTL)

        # Check if over cap, evict oldest
        evicted = []
        excess = redis.zcard(register_key) - cap
        if excess > 0:
            oldest = redis.zpopmin(register_key, count=excess)
            evicted = [item[0].decode() for item in oldest]
            for evicted_jti in evicted:
                blacklist_key = BLACKLIST_KEY.format(jti=evicted_jti)
                redis.set(blacklist_key, 1, ex=TOKEN_TTL)
                meta_key = META_KEY.format(jti=evicted_jti)
                redis.delete(meta_key)

        return evicted

    @staticmethod
    def evict_session(user_id, jti):
        blacklist_key = BLACKLIST_KEY.format(jti=jti)
        register_key = REGISTER_KEY.format(user_id=user_id)
        redis = cast(Redis, get_redis_connection("default"))

        # Add to blacklist
        redis.set(blacklist_key, 1, ex=TOKEN_TTL)

        # Remove from registry
        redis.zrem(register_key, jti)

        # Delete metadata
        meta_key = META_KEY.format(jti=jti)
        redis.delete(meta_key)

    @staticmethod
    def is_evicted(jti) -> bool:
        redis = cast(Redis, get_redis_connection("default"))
        blacklist_key = BLACKLIST_KEY.format(jti=jti)
        return redis.exists(blacklist_key) > 0
    
    @staticmethod
    def list_sessions(user_id) -> list[dict]:
        redis = cast(Redis, get_redis_connection("default"))
        register_key = REGISTER_KEY.format(user_id=user_id)

        jtis = redis.zrange(register_key, 0, -1)

        sessions = []
        for jti_bytes in jtis:
            jti = jti_bytes.decode()
            meta_key = META_KEY.format(jti=jti)
            meta_dict = redis.hgetall(meta_key)
            sessions.append({
                "jti": jti,
                "ip": (meta_dict.get(b"ip") or b"").decode(),
                "user_agent": (meta_dict.get(b"user_agent") or b"").decode(),
                "created_at": (meta_dict.get(b"created_at") or b"").decode(),
                })

        return sessions

    @staticmethod
    def remove_session(user_id, jti):
        """Remove a session from the registry without blacklisting it (for normal logout)."""
        register_key = REGISTER_KEY.format(user_id=user_id)
        redis = cast(Redis, get_redis_connection("default"))
        removed = redis.zrem(register_key, jti)
        meta_key = META_KEY.format(jti=jti)
        redis.delete(meta_key)
        return removed

    @staticmethod
    def delete_session(user_id, jti):
        SessionRegistry.evict_session(user_id, jti)
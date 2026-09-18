import logging
import time
from typing import cast
from redis import Redis
from django_redis import get_redis_connection

logger = logging.getLogger(__name__)

TOKEN_TTL = 60 * 60 * 24 * 4 # 4 days, same as JWT refresh token lifetime

REGISTER_KEY = "session:registry:{user_id}"
META_KEY = "session:meta:{jti}"
BLACKLIST_KEY = "session:blacklist:{jti}"

REGISTER_AND_EVICT_LUA = """
local register_key = KEYS[1]
local jti = ARGV[1]
local score = ARGV[2]
local cap = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local blacklist_prefix = ARGV[5]
local meta_prefix = ARGV[6]

redis.call("ZADD", register_key, score, jti)
redis.call("EXPIRE", register_key, ttl)

local count = redis.call("ZCARD", register_key)
local excess = count - cap

local evicted = {}
if excess > 0 then
    local oldest = redis.call("ZPOPMIN", register_key, excess)
    for i = 1, #oldest, 2 do
        local evicted_jti = oldest[i]
        redis.call("SET", blacklist_prefix .. evicted_jti, 1, "EX", ttl)
        redis.call("DEL", meta_prefix .. evicted_jti)
        table.insert(evicted, evicted_jti)
    end
end

return evicted
"""

class SessionRegistry:

    @staticmethod
    def register_session(user_id, jti, meta: dict, cap: int) -> list[str]:
        redis = cast(Redis, get_redis_connection("default"))
        register_key = REGISTER_KEY.format(user_id = user_id)
        meta_key = META_KEY.format(jti = jti)

        redis.hset(meta_key, mapping={
            "ip": meta.get("ip", ""),
            "user_agent": meta.get("user_agent", ""),
            "created_at": meta.get("created_at", ""),
        })
        redis.expire(meta_key, TOKEN_TTL)

        evicted_raw = redis.eval(
            REGISTER_AND_EVICT_LUA,
            1,
            register_key,
            jti,
            time.time(),
            cap,
            TOKEN_TTL,
            "session:blacklist:",
            "session:meta:"
        )

        evicted = [v.decode() for v in evicted_raw]

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

        # Notify active WebSocket connections to disconnect immediately
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer
            channel_layer = get_channel_layer()
            if channel_layer is not None:
                async_to_sync(channel_layer.group_send)(
                    f'chat_user_{user_id}',
                    {
                        'type': 'user_session_revoked',
                        'reason': 'session_evicted',
                    },
                )
        except Exception:
            logger.exception(
                "Failed to emit session eviction websocket for user %s", user_id
            )

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
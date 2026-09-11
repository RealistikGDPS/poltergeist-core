import hashlib
import hmac

from poltergeist_core.adapters.redis import RedisClient


def _digest(gjp2: str) -> str:
    return hashlib.sha256(gjp2.encode()).hexdigest()


class SessionRepository:
    """Remembers that a gjp2 was verified against bcrypt, so hot requests skip
    the expensive check. Only a digest of the gjp2 is stored."""

    __slots__ = ("_redis",)

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    @staticmethod
    def _key(user_id: int) -> str:
        return f"session:{user_id}"

    async def is_verified(self, user_id: int, gjp2: str) -> bool:
        stored = await self._redis.get(self._key(user_id))

        if stored is None:
            return False

        return hmac.compare_digest(str(stored), _digest(gjp2))

    async def mark_verified(self, user_id: int, gjp2: str, *, seconds: int) -> None:
        await self._redis.set(self._key(user_id), _digest(gjp2), ex=seconds)

    async def revoke(self, user_id: int) -> None:
        await self._redis.delete(self._key(user_id))

import hashlib
import secrets

from poltergeist_core.adapters.redis import RedisClient

_TOKEN_BYTES = 32


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class WebSessionRepository:
    """Opaque browser sessions. Only a digest of the token is stored, so a
    copy of the cache cannot be turned into a cookie."""

    __slots__ = ("_redis",)

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    @staticmethod
    def _key(digest: str) -> str:
        return f"web_session:{digest}"

    @staticmethod
    def _members_key(user_id: int) -> str:
        return f"web_sessions:{user_id}"

    async def create(self, user_id: int, *, seconds: int) -> str:
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        digest = _digest(token)

        async with self._redis.pipeline(transaction=True) as pipeline:
            pipeline.set(self._key(digest), str(user_id), ex=seconds)
            pipeline.sadd(self._members_key(user_id), digest)
            pipeline.expire(self._members_key(user_id), seconds)
            await pipeline.execute()

        return token

    async def resolve(self, token: str, *, seconds: int) -> int | None:
        """Every successful lookup pushes the expiry forward again."""

        stored = await self._redis.getex(self._key(_digest(token)), ex=seconds)

        if stored is None:
            return None

        user_id = int(str(stored))
        await self._redis.expire(self._members_key(user_id), seconds)

        return user_id

    async def revoke(self, token: str) -> None:
        digest = _digest(token)
        stored = await self._redis.get(self._key(digest))

        if stored is None:
            return

        async with self._redis.pipeline(transaction=True) as pipeline:
            pipeline.delete(self._key(digest))
            pipeline.srem(self._members_key(int(str(stored))), digest)
            await pipeline.execute()

    async def revoke_all(self, user_id: int) -> int:
        digests = await self._redis.smembers(self._members_key(user_id))

        async with self._redis.pipeline(transaction=True) as pipeline:
            for digest in digests:
                pipeline.delete(self._key(str(digest)))

            pipeline.delete(self._members_key(user_id))
            await pipeline.execute()

        return len(digests)

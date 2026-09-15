from poltergeist_core.adapters.redis import RedisClient

_MISSING_SECONDS = 600
_SUSPENDED_SECONDS = 60

_SUSPENDED_KEY = "boomlings:suspended"


class SongLookupRepository:
    """Remembers upstream song ids that were not found, and whether the official
    servers are answering at all, so they are not asked again for a while."""

    __slots__ = ("_redis",)

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def is_missing(self, song_id: int) -> bool:
        return bool(await self._redis.exists(f"song:missing:{song_id}"))

    async def mark_missing(self, song_id: int) -> None:
        await self._redis.set(f"song:missing:{song_id}", "1", ex=_MISSING_SECONDS)

    async def is_suspended(self) -> bool:
        return bool(await self._redis.exists(_SUSPENDED_KEY))

    async def suspend(self) -> None:
        await self._redis.set(_SUSPENDED_KEY, "1", ex=_SUSPENDED_SECONDS)

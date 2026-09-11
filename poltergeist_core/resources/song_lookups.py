from poltergeist_core.adapters.redis import RedisClient

_MISSING_SECONDS = 600


class SongLookupRepository:
    """Remembers upstream song ids that were not found so the official servers
    are not asked again for a while."""

    __slots__ = ("_redis",)

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def is_missing(self, song_id: int) -> bool:
        return bool(await self._redis.exists(f"song:missing:{song_id}"))

    async def mark_missing(self, song_id: int) -> None:
        await self._redis.set(f"song:missing:{song_id}", "1", ex=_MISSING_SECONDS)

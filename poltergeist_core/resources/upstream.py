from poltergeist_core.adapters.redis import RedisClient

_MISSING_SECONDS = 600
_SUSPENDED_SECONDS = 60
_CLAIM_SECONDS = 60

_SUSPENDED_KEY = "boomlings:suspended"


class UpstreamRepository:
    """What is known about the official servers: whether they answer at all,
    which ids they do not have, and which levels are being fetched right now."""

    __slots__ = ("_redis",)

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def is_suspended(self) -> bool:
        return bool(await self._redis.exists(_SUSPENDED_KEY))

    async def suspend(self) -> None:
        await self._redis.set(_SUSPENDED_KEY, "1", ex=_SUSPENDED_SECONDS)

    async def is_song_missing(self, song_id: int) -> bool:
        return bool(await self._redis.exists(f"song:missing:{song_id}"))

    async def mark_song_missing(self, song_id: int) -> None:
        await self._redis.set(f"song:missing:{song_id}", "1", ex=_MISSING_SECONDS)

    async def is_level_missing(self, level_id: int) -> bool:
        return bool(await self._redis.exists(f"level:missing:{level_id}"))

    async def mark_level_missing(self, level_id: int) -> None:
        await self._redis.set(f"level:missing:{level_id}", "1", ex=_MISSING_SECONDS)

    async def claim_level(self, level_id: int) -> bool:
        claimed = await self._redis.set(
            f"level:claim:{level_id}", "1", nx=True, ex=_CLAIM_SECONDS
        )

        return bool(claimed)

    async def release_level(self, level_id: int) -> None:
        await self._redis.delete(f"level:claim:{level_id}")

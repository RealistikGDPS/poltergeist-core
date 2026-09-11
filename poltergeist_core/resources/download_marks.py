from poltergeist_core.adapters.redis import RedisClient

_MARK_SECONDS = 86_400


class DownloadMarkRepository:
    """Deduplicates download counting per user and day."""

    __slots__ = ("_redis",)

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def mark(self, kind: str, target_id: int, user_id: int) -> bool:
        """Whether this is the first download by the user today."""

        name = f"downloads:{kind}:{target_id}"

        async with self._redis.pipeline(transaction=True) as pipeline:
            pipeline.sadd(name, str(user_id))
            pipeline.expire(name, _MARK_SECONDS, nx=True)
            added, _ = await pipeline.execute()

        return int(added) == 1

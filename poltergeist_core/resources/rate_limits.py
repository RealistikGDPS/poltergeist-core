from poltergeist_core.adapters.redis import RedisClient


class RateLimitRepository:
    """Fixed-window counters."""

    __slots__ = ("_redis",)

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def hit(
        self, scope: str, key: str, *, limit: int, window_seconds: int
    ) -> bool:
        """Records one hit and reports whether the caller is still within the
        limit for the window."""

        name = f"ratelimit:{scope}:{key}"

        async with self._redis.pipeline(transaction=True) as pipeline:
            pipeline.incr(name)
            pipeline.expire(name, window_seconds, nx=True)
            count, _ = await pipeline.execute()

        return int(count) <= limit

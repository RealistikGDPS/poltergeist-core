from poltergeist_core.adapters.redis import RedisClient
from poltergeist_core.utilities import clock

_KEY = "presence"


class PresenceRepository:
    """Who was seen recently, as one sorted set scored by the last request.
    Stale members are trimmed whenever someone is seen, so the set never holds
    more than a window's worth of players."""

    __slots__ = ("_redis",)  # Built on every context property access.

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def touch(self, user_id: int, *, window_seconds: int) -> None:
        now = clock.timestamp(clock.now())

        async with self._redis.pipeline(transaction=False) as pipeline:
            pipeline.zadd(_KEY, {str(user_id): now})
            pipeline.execute_command(
                "ZREMRANGEBYSCORE", _KEY, "-inf", now - window_seconds
            )
            await pipeline.execute()

    async def count_since(self, seconds: int) -> int:
        since = clock.timestamp(clock.now()) - seconds
        count = await self._redis.execute_command("ZCOUNT", _KEY, since, "+inf")

        return int(count)

    async def online_among(self, user_ids: list[int], seconds: int) -> set[int]:
        if not user_ids:
            return set()

        since = clock.timestamp(clock.now()) - seconds

        async with self._redis.pipeline(transaction=False) as pipeline:
            for user_id in user_ids:
                pipeline.execute_command("ZSCORE", _KEY, str(user_id))

            scores = await pipeline.execute()

        return {
            user_id
            for user_id, score in zip(user_ids, scores, strict=True)
            if score is not None and float(score) >= since
        }

from datetime import date
from datetime import timedelta
from enum import StrEnum

from poltergeist_core.adapters.redis import RedisClient
from poltergeist_core.resources._common import offset

# A week of comparisons plus a margin, so last week's board is always there.
_SNAPSHOT_SECONDS = 9 * 86_400


class LeaderboardKind(StrEnum):
    STARS = "stars"
    MOONS = "moons"
    DEMONS = "demons"
    USER_COINS = "user_coins"
    CREATOR_POINTS = "creator_points"


def _rank_from(value: object) -> int | None:
    """redis-py types command results loosely; ranks are ints when present."""

    if isinstance(value, int):
        return value + 1

    return None


def _members(values: object) -> list[int]:
    if not isinstance(values, list):
        return []

    return [int(str(member)) for member in values]


class LeaderboardRepository:
    """Global rankings as Redis sorted sets, one per statistic."""

    __slots__ = ("_redis",)

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    @staticmethod
    def _key(kind: LeaderboardKind) -> str:
        return f"leaderboard:{kind.value}"

    async def set_score(self, kind: LeaderboardKind, user_id: int, value: int) -> None:
        if value <= 0:
            await self._redis.zrem(self._key(kind), str(user_id))

            return

        await self._redis.zadd(self._key(kind), {str(user_id): value})

    async def set_scores(
        self, user_id: int, values: dict[LeaderboardKind, int]
    ) -> None:
        async with self._redis.pipeline(transaction=False) as pipeline:
            for kind, value in values.items():
                if value <= 0:
                    pipeline.zrem(self._key(kind), str(user_id))
                else:
                    pipeline.zadd(self._key(kind), {str(user_id): value})

            await pipeline.execute()

    async def remove(self, user_id: int) -> None:
        async with self._redis.pipeline(transaction=False) as pipeline:
            for kind in LeaderboardKind:
                pipeline.zrem(self._key(kind), str(user_id))

            await pipeline.execute()

    async def rank(self, kind: LeaderboardKind, user_id: int) -> int | None:
        return _rank_from(await self._redis.zrevrank(self._key(kind), str(user_id)))

    async def ranks(self, kind: LeaderboardKind, user_ids: list[int]) -> dict[int, int]:
        if not user_ids:
            return {}

        async with self._redis.pipeline(transaction=False) as pipeline:
            for user_id in user_ids:
                pipeline.zrevrank(self._key(kind), str(user_id))

            ranks = await pipeline.execute()

        resolved = {
            user_id: _rank_from(rank)
            for user_id, rank in zip(user_ids, ranks, strict=True)
        }

        return {user_id: rank for user_id, rank in resolved.items() if rank is not None}

    async def top(self, kind: LeaderboardKind, count: int) -> list[int]:
        return _members(await self._redis.zrevrange(self._key(kind), 0, count - 1))

    async def page(self, kind: LeaderboardKind, page: int, size: int) -> list[int]:
        start = offset(page, size)

        return _members(
            await self._redis.zrevrange(self._key(kind), start, start + size - 1)
        )

    async def count(self, kind: LeaderboardKind) -> int:
        return int(await self._redis.zcard(self._key(kind)))

    async def ranks_of(self, user_id: int) -> dict[LeaderboardKind, int]:
        """The user's position on every board they appear on."""

        async with self._redis.pipeline(transaction=False) as pipeline:
            for kind in LeaderboardKind:
                pipeline.zrevrank(self._key(kind), str(user_id))

            ranks = await pipeline.execute()

        resolved = {
            kind: _rank_from(rank)
            for kind, rank in zip(LeaderboardKind, ranks, strict=True)
        }

        return {kind: rank for kind, rank in resolved.items() if rank is not None}

    async def around(
        self, kind: LeaderboardKind, user_id: int, count: int
    ) -> list[int]:
        rank = _rank_from(await self._redis.zrevrank(self._key(kind), str(user_id)))

        if rank is None:
            return []

        start = max(rank - 1 - count // 2, 0)

        return _members(
            await self._redis.zrevrange(self._key(kind), start, start + count - 1)
        )

    async def clear(self, kind: LeaderboardKind) -> None:
        await self._redis.delete(self._key(kind))

    @staticmethod
    def _snapshot_key(kind: LeaderboardKind, day: date) -> str:
        return f"leaderboard:{kind.value}:{day.isoformat()}"

    async def snapshot(self, kind: LeaderboardKind, day: date) -> None:
        """Freezes the board as it stands under the day's name. Only the first
        call of a day copies; later ones leave that copy alone."""

        key = self._snapshot_key(kind, day)

        async with self._redis.pipeline(transaction=False) as pipeline:
            pipeline.execute_command("COPY", self._key(kind), key)
            pipeline.expire(key, _SNAPSHOT_SECONDS, nx=True)
            await pipeline.execute()

    async def oldest_snapshot(
        self, kind: LeaderboardKind, today: date, days: int
    ) -> date | None:
        """The earliest frozen board within `days` before today."""

        candidates = [today - timedelta(days=back) for back in range(days, 0, -1)]

        async with self._redis.pipeline(transaction=False) as pipeline:
            for day in candidates:
                pipeline.exists(self._snapshot_key(kind, day))

            found = await pipeline.execute()

        for day, exists in zip(candidates, found, strict=True):
            if int(exists):
                return day

        return None

    async def snapshot_ranks(
        self, kind: LeaderboardKind, day: date, user_ids: list[int]
    ) -> dict[int, int]:
        if not user_ids:
            return {}

        key = self._snapshot_key(kind, day)

        async with self._redis.pipeline(transaction=False) as pipeline:
            for user_id in user_ids:
                pipeline.zrevrank(key, str(user_id))

            ranks = await pipeline.execute()

        resolved = {
            user_id: _rank_from(rank)
            for user_id, rank in zip(user_ids, ranks, strict=True)
        }

        return {user_id: rank for user_id, rank in resolved.items() if rank is not None}

from datetime import datetime
from enum import StrEnum

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset

_COUNTERS = (
    "stars, moons, demons, diamonds, secret_coins, user_coins, demons_easy, "
    "demons_medium, demons_hard, demons_insane, demons_extreme, "
    "demons_easy_platformer, demons_medium_platformer, demons_hard_platformer, "
    "demons_insane_platformer, demons_extreme_platformer, demons_weekly, "
    "demons_gauntlet, demons_event"
)
_COLUMNS = f"id, user_id, source, {_COUNTERS}, created_at"


class StatsSource(StrEnum):
    BASELINE = "baseline"
    CLIENT = "client"
    RESTORE = "restore"


class StatsSnapshot(Model):
    """The counters a client can inflate, as they stood after one update."""

    stars: int
    moons: int
    demons: int
    diamonds: int
    secret_coins: int
    user_coins: int
    demons_easy: int
    demons_medium: int
    demons_hard: int
    demons_insane: int
    demons_extreme: int
    demons_easy_platformer: int
    demons_medium_platformer: int
    demons_hard_platformer: int
    demons_insane_platformer: int
    demons_extreme_platformer: int
    demons_weekly: int
    demons_gauntlet: int
    demons_event: int


class StatsHistoryEntry(StatsSnapshot):
    id: int
    user_id: int
    source: StatsSource
    created_at: datetime

    @property
    def snapshot(self) -> StatsSnapshot:
        return StatsSnapshot.model_validate(self.model_dump())


class StatsHistoryRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def create(
        self, user_id: int, source: StatsSource, snapshot: StatsSnapshot
    ) -> int:
        values = snapshot.model_dump()
        columns = ", ".join(values)
        placeholders = ", ".join(f"%({column})s" for column in values)

        result = await self._mysql.execute(
            f"INSERT INTO user_stats_history (user_id, source, {columns}) "
            f"VALUES (%(user_id)s, %(source)s, {placeholders})",
            {**values, "user_id": user_id, "source": source.value},
        )

        return result.last_row_id

    async def find_by_id(self, history_id: int) -> StatsHistoryEntry | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM user_stats_history WHERE id = %(id)s",
            {"id": history_id},
        )

        return None if row is None else StatsHistoryEntry.model_validate(row)

    async def latest(self, user_id: int) -> StatsHistoryEntry | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM user_stats_history WHERE user_id = %(id)s "
            "ORDER BY id DESC LIMIT 1",
            {"id": user_id},
        )

        return None if row is None else StatsHistoryEntry.model_validate(row)

    async def find_before(
        self, user_id: int, history_id: int
    ) -> StatsHistoryEntry | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM user_stats_history WHERE user_id = %(id)s "
            "AND id < %(before)s ORDER BY id DESC LIMIT 1",
            {"id": user_id, "before": history_id},
        )

        return None if row is None else StatsHistoryEntry.model_validate(row)

    async def list_by_user(
        self, user_id: int, page: int, size: int
    ) -> list[StatsHistoryEntry]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM user_stats_history WHERE user_id = %(id)s "
            "ORDER BY id DESC LIMIT %(limit)s OFFSET %(offset)s",
            {"id": user_id, "limit": size, "offset": offset(page, size)},
        )

        return [StatsHistoryEntry.model_validate(row) for row in rows]

    async def count_by_user(self, user_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM user_stats_history WHERE user_id = %(id)s",
            {"id": user_id},
        )

        return count

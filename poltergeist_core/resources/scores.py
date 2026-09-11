import json
from datetime import datetime
from datetime import timedelta

from gdformat.enums import PlatformerMode

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import JsonIntList
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import placeholders
from poltergeist_core.utilities import clock

_CLASSIC_COLUMNS = (
    "id, level_id, user_id, timely_level_id, percent, attempts, clicks, seconds, "
    "coins, progress, level_version, submitted_at"
)
_PLATFORMER_COLUMNS = (
    "id, level_id, user_id, timely_level_id, time_ms, points, attempts, clicks, "
    "coins, level_version, submitted_at"
)
_WEEK = timedelta(days=7)


class LevelScore(Model):
    id: int
    level_id: int
    user_id: int
    timely_level_id: int | None
    percent: int
    attempts: int
    clicks: int
    seconds: int
    coins: int
    progress: JsonIntList
    level_version: int
    submitted_at: datetime


class PlatformerScore(Model):
    id: int
    level_id: int
    user_id: int
    timely_level_id: int | None
    time_ms: int
    points: int
    attempts: int
    clicks: int
    coins: int
    level_version: int
    submitted_at: datetime


def _timely_sql(timely_level_id: int | None) -> str:
    if timely_level_id is None:
        return "timely_level_id IS NULL"

    return "timely_level_id = %(timely)s"


class LevelScoreRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find(
        self, level_id: int, user_id: int, timely_level_id: int | None
    ) -> LevelScore | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_CLASSIC_COLUMNS} FROM level_scores WHERE level_id = %(level)s "
            f"AND user_id = %(user)s AND {_timely_sql(timely_level_id)} "
            "AND deleted_at IS NULL",
            {"level": level_id, "user": user_id, "timely": timely_level_id},
        )

        return None if row is None else LevelScore.model_validate(row)

    async def upsert(
        self,
        *,
        level_id: int,
        user_id: int,
        timely_level_id: int | None,
        percent: int,
        attempts: int,
        clicks: int,
        seconds: int,
        coins: int,
        progress: list[int],
        level_version: int,
    ) -> None:
        await self._mysql.execute(
            "INSERT INTO level_scores (level_id, user_id, timely_level_id, percent, "
            "attempts, clicks, seconds, coins, progress, level_version, submitted_at) "
            "VALUES (%(level)s, %(user)s, %(timely)s, %(percent)s, %(attempts)s, "
            "%(clicks)s, %(seconds)s, %(coins)s, %(progress)s, %(version)s, %(now)s) "
            "ON DUPLICATE KEY UPDATE percent = VALUES(percent), "
            "attempts = VALUES(attempts), clicks = VALUES(clicks), "
            "seconds = VALUES(seconds), coins = VALUES(coins), "
            "progress = VALUES(progress), level_version = VALUES(level_version), "
            "submitted_at = VALUES(submitted_at), deleted_at = NULL",
            {
                "level": level_id,
                "user": user_id,
                "timely": timely_level_id,
                "percent": percent,
                "attempts": attempts,
                "clicks": clicks,
                "seconds": seconds,
                "coins": coins,
                "progress": json.dumps(progress),
                "version": level_version,
                "now": clock.now(),
            },
        )

    async def list_top(
        self, level_id: int, timely_level_id: int | None, limit: int
    ) -> list[LevelScore]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_CLASSIC_COLUMNS} FROM level_scores WHERE level_id = %(level)s "
            f"AND {_timely_sql(timely_level_id)} AND deleted_at IS NULL "
            "ORDER BY percent DESC, submitted_at LIMIT %(limit)s",
            {"level": level_id, "timely": timely_level_id, "limit": limit},
        )

        return [LevelScore.model_validate(row) for row in rows]

    async def list_week(self, level_id: int, limit: int) -> list[LevelScore]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_CLASSIC_COLUMNS} FROM level_scores WHERE level_id = %(level)s "
            "AND timely_level_id IS NULL AND deleted_at IS NULL "
            "AND submitted_at >= %(since)s ORDER BY percent DESC, submitted_at "
            "LIMIT %(limit)s",
            {"level": level_id, "since": clock.now() - _WEEK, "limit": limit},
        )

        return [LevelScore.model_validate(row) for row in rows]

    async def list_friends(
        self, level_id: int, timely_level_id: int | None, user_ids: list[int]
    ) -> list[LevelScore]:
        if not user_ids:
            return []

        sql, values = placeholders(user_ids, "user")

        rows = await self._mysql.fetch_all(
            f"SELECT {_CLASSIC_COLUMNS} FROM level_scores WHERE level_id = %(level)s "
            f"AND {_timely_sql(timely_level_id)} AND user_id IN ({sql}) "
            "AND deleted_at IS NULL ORDER BY percent DESC, submitted_at",
            {**values, "level": level_id, "timely": timely_level_id},
        )

        return [LevelScore.model_validate(row) for row in rows]

    async def soft_delete_by_user(self, user_id: int) -> None:
        await self._mysql.execute(
            "UPDATE level_scores SET deleted_at = %(now)s WHERE user_id = %(id)s "
            "AND deleted_at IS NULL",
            {"id": user_id, "now": clock.now()},
        )


class PlatformerScoreRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find(
        self, level_id: int, user_id: int, timely_level_id: int | None
    ) -> PlatformerScore | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_PLATFORMER_COLUMNS} FROM level_platformer_scores "
            f"WHERE level_id = %(level)s AND user_id = %(user)s "
            f"AND {_timely_sql(timely_level_id)} AND deleted_at IS NULL",
            {"level": level_id, "user": user_id, "timely": timely_level_id},
        )

        return None if row is None else PlatformerScore.model_validate(row)

    async def upsert(
        self,
        *,
        level_id: int,
        user_id: int,
        timely_level_id: int | None,
        time_ms: int,
        points: int,
        attempts: int,
        clicks: int,
        coins: int,
        level_version: int,
    ) -> None:
        await self._mysql.execute(
            "INSERT INTO level_platformer_scores (level_id, user_id, timely_level_id, "
            "time_ms, points, attempts, clicks, coins, level_version, submitted_at) "
            "VALUES (%(level)s, %(user)s, %(timely)s, %(time)s, %(points)s, "
            "%(attempts)s, %(clicks)s, %(coins)s, %(version)s, %(now)s) "
            "ON DUPLICATE KEY UPDATE time_ms = VALUES(time_ms), "
            "points = VALUES(points), attempts = VALUES(attempts), "
            "clicks = VALUES(clicks), coins = VALUES(coins), "
            "level_version = VALUES(level_version), "
            "submitted_at = VALUES(submitted_at), deleted_at = NULL",
            {
                "level": level_id,
                "user": user_id,
                "timely": timely_level_id,
                "time": time_ms,
                "points": points,
                "attempts": attempts,
                "clicks": clicks,
                "coins": coins,
                "version": level_version,
                "now": clock.now(),
            },
        )

    def _order(self, mode: PlatformerMode) -> str:
        match mode:
            case PlatformerMode.TIME:
                return "time_ms ASC, submitted_at"
            case PlatformerMode.POINTS:
                return "points DESC, submitted_at"

    async def list_top(
        self,
        level_id: int,
        timely_level_id: int | None,
        mode: PlatformerMode,
        limit: int,
    ) -> list[PlatformerScore]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_PLATFORMER_COLUMNS} FROM level_platformer_scores "
            f"WHERE level_id = %(level)s AND {_timely_sql(timely_level_id)} "
            f"AND deleted_at IS NULL ORDER BY {self._order(mode)} LIMIT %(limit)s",
            {"level": level_id, "timely": timely_level_id, "limit": limit},
        )

        return [PlatformerScore.model_validate(row) for row in rows]

    async def list_week(
        self, level_id: int, mode: PlatformerMode, limit: int
    ) -> list[PlatformerScore]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_PLATFORMER_COLUMNS} FROM level_platformer_scores "
            "WHERE level_id = %(level)s AND timely_level_id IS NULL "
            "AND deleted_at IS NULL AND submitted_at >= %(since)s "
            f"ORDER BY {self._order(mode)} LIMIT %(limit)s",
            {"level": level_id, "since": clock.now() - _WEEK, "limit": limit},
        )

        return [PlatformerScore.model_validate(row) for row in rows]

    async def list_friends(
        self,
        level_id: int,
        timely_level_id: int | None,
        mode: PlatformerMode,
        user_ids: list[int],
    ) -> list[PlatformerScore]:
        if not user_ids:
            return []

        sql, values = placeholders(user_ids, "user")

        rows = await self._mysql.fetch_all(
            f"SELECT {_PLATFORMER_COLUMNS} FROM level_platformer_scores "
            f"WHERE level_id = %(level)s AND {_timely_sql(timely_level_id)} "
            f"AND user_id IN ({sql}) AND deleted_at IS NULL "
            f"ORDER BY {self._order(mode)}",
            {**values, "level": level_id, "timely": timely_level_id},
        )

        return [PlatformerScore.model_validate(row) for row in rows]

    async def soft_delete_by_user(self, user_id: int) -> None:
        await self._mysql.execute(
            "UPDATE level_platformer_scores SET deleted_at = %(now)s "
            "WHERE user_id = %(id)s AND deleted_at IS NULL",
            {"id": user_id, "now": clock.now()},
        )

from datetime import date
from datetime import datetime
from datetime import timedelta

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.adapters.redis import RedisClient
from poltergeist_core.resources._common import Model
from poltergeist_core.utilities import clock

_SNAPSHOT_KEY = "analytics:snapshot"


class Totals(Model):
    users: int
    levels: int
    rated_levels: int
    featured_levels: int
    comments: int
    account_comments: int
    messages: int
    songs: int
    active_bans: int
    saves: int


class DailyCount(Model):
    day: date
    count: int


class LabelCount(Model):
    label: int
    count: int


class CreatorRow(Model):
    user_id: int
    username: str
    levels: int
    rated: int
    creator_points: int


class Snapshot(Model):
    """The public statistics page, built once and served from the cache."""

    totals: Totals
    registrations: list[DailyCount]
    uploads: list[DailyCount]
    active_users: list[DailyCount]
    difficulties: list[LabelCount]
    top_creators: list[CreatorRow]
    generated_at: datetime


class AnalyticsRepository:
    """Aggregate queries for the administration panel and the public site."""

    __slots__ = ("_mysql", "_redis")

    def __init__(self, mysql: ImplementsMySQL, redis: RedisClient) -> None:
        self._mysql = mysql
        self._redis = redis

    async def find_snapshot(self) -> Snapshot | None:
        stored = await self._redis.get(_SNAPSHOT_KEY)

        return None if stored is None else Snapshot.model_validate_json(str(stored))

    async def store_snapshot(self, snapshot: Snapshot, *, seconds: int) -> None:
        await self._redis.set(_SNAPSHOT_KEY, snapshot.model_dump_json(), ex=seconds)

    async def totals(self) -> Totals:
        row = await self._mysql.fetch_one(
            "SELECT "
            "(SELECT COUNT(*) FROM users WHERE deleted_at IS NULL) AS users, "
            "(SELECT COUNT(*) FROM levels WHERE deleted_at IS NULL) AS levels, "
            "(SELECT COUNT(*) FROM levels WHERE deleted_at IS NULL AND stars > 0) "
            "AS rated_levels, "
            "(SELECT COUNT(*) FROM levels WHERE deleted_at IS NULL AND feature_order "
            "> 0) "
            "AS featured_levels, "
            "(SELECT COUNT(*) FROM comments WHERE deleted_at IS NULL) AS comments, "
            "(SELECT COUNT(*) FROM account_comments WHERE deleted_at IS NULL) "
            "AS account_comments, "
            "(SELECT COUNT(*) FROM messages) AS messages, "
            "(SELECT COUNT(*) FROM songs WHERE deleted_at IS NULL) AS songs, "
            "(SELECT COUNT(*) FROM user_bans WHERE revoked_at IS NULL "
            "AND (expires_at IS NULL OR expires_at > %(now)s)) AS active_bans, "
            "(SELECT COUNT(*) FROM user_saves) AS saves",
            {"now": clock.now()},
        )
        assert row is not None, "An aggregate query always yields one row."

        return Totals.model_validate(row)

    async def _per_day(self, table: str, column: str, days: int) -> list[DailyCount]:
        rows = await self._mysql.fetch_all(
            f"SELECT DATE({column}) AS day, COUNT(*) AS count FROM {table} "
            f"WHERE {column} >= %(since)s GROUP BY DATE({column}) ORDER BY day",
            {"since": clock.now() - timedelta(days=days)},
        )

        return [DailyCount.model_validate(row) for row in rows]

    async def registrations_per_day(self, days: int) -> list[DailyCount]:
        return await self._per_day("users", "registered_at", days)

    async def uploads_per_day(self, days: int) -> list[DailyCount]:
        return await self._per_day("levels", "uploaded_at", days)

    async def comments_per_day(self, days: int) -> list[DailyCount]:
        return await self._per_day("comments", "created_at", days)

    async def active_users_per_day(self, days: int) -> list[DailyCount]:
        return await self._per_day("users", "last_seen_at", days)

    async def mod_actions_per_day(self, days: int) -> list[DailyCount]:
        return await self._per_day("mod_actions", "created_at", days)

    async def comments_per_hour(self, days: int) -> list[LabelCount]:
        rows = await self._mysql.fetch_all(
            "SELECT HOUR(created_at) AS label, COUNT(*) AS count FROM comments "
            "WHERE created_at >= %(since)s GROUP BY HOUR(created_at) ORDER BY label",
            {"since": clock.now() - timedelta(days=days)},
        )

        return [LabelCount.model_validate(row) for row in rows]

    async def difficulty_distribution(self) -> list[LabelCount]:
        rows = await self._mysql.fetch_all(
            "SELECT difficulty AS label, COUNT(*) AS count FROM levels "
            "WHERE deleted_at IS NULL GROUP BY difficulty ORDER BY difficulty"
        )

        return [LabelCount.model_validate(row) for row in rows]

    async def length_distribution(self) -> list[LabelCount]:
        rows = await self._mysql.fetch_all(
            "SELECT length AS label, COUNT(*) AS count FROM levels "
            "WHERE deleted_at IS NULL GROUP BY length ORDER BY length"
        )

        return [LabelCount.model_validate(row) for row in rows]

    async def stars_distribution(self) -> list[LabelCount]:
        rows = await self._mysql.fetch_all(
            "SELECT stars AS label, COUNT(*) AS count FROM levels "
            "WHERE deleted_at IS NULL AND stars > 0 GROUP BY stars ORDER BY stars"
        )

        return [LabelCount.model_validate(row) for row in rows]

    async def top_creators(self, limit: int) -> list[CreatorRow]:
        rows = await self._mysql.fetch_all(
            "SELECT u.id AS user_id, u.username, COUNT(l.id) AS levels, "
            "SUM(l.stars > 0) AS rated, s.creator_points FROM users u "
            "JOIN user_stats s ON s.user_id = u.id "
            "JOIN levels l ON l.user_id = u.id AND l.deleted_at IS NULL "
            "WHERE u.deleted_at IS NULL GROUP BY u.id, u.username, s.creator_points "
            "ORDER BY s.creator_points DESC, levels DESC LIMIT %(limit)s",
            {"limit": limit},
        )

        return [CreatorRow.model_validate(row) for row in rows]

import json
from datetime import datetime
from enum import StrEnum

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import JsonObject
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset
from poltergeist_core.resources._common import placeholders
from poltergeist_core.utilities import clock

_COLUMNS = (
    "id, user_id, kind, status, target_id, evidence, created_at, resolved_at, "
    "resolved_by_user_id"
)


class FlagKind(StrEnum):
    STATS_CEILING = "stats_ceiling"
    SCORE_IMPLAUSIBLE = "score_implausible"
    ALT_ACCOUNT = "alt_account"


class FlagStatus(StrEnum):
    OPEN = "open"
    DISMISSED = "dismissed"
    ACTIONED = "actioned"


class UserFlag(Model):
    id: int
    user_id: int
    kind: FlagKind
    status: FlagStatus
    target_id: int | None
    evidence: JsonObject
    created_at: datetime
    resolved_at: datetime | None
    resolved_by_user_id: int | None


class FlagRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def create(
        self,
        user_id: int,
        kind: FlagKind,
        *,
        target_id: int | None,
        evidence: dict[str, object],
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO user_flags (user_id, kind, target_id, evidence) "
            "VALUES (%(user)s, %(kind)s, %(target)s, %(evidence)s)",
            {
                "user": user_id,
                "kind": kind.value,
                "target": target_id,
                "evidence": json.dumps(evidence),
            },
        )

        return result.last_row_id

    async def find_by_id(self, flag_id: int) -> UserFlag | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM user_flags WHERE id = %(id)s", {"id": flag_id}
        )

        return None if row is None else UserFlag.model_validate(row)

    async def find_many_by_ids(self, flag_ids: list[int]) -> list[UserFlag]:
        if not flag_ids:
            return []

        sql, values = placeholders(flag_ids, "f")

        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM user_flags WHERE id IN ({sql})", values
        )

        return [UserFlag.model_validate(row) for row in rows]

    async def exists_open(
        self, user_id: int, kind: FlagKind, *, target_id: int | None
    ) -> bool:
        value = await self._mysql.fetch_val(
            "SELECT 1 FROM user_flags WHERE user_id = %(user)s AND kind = %(kind)s "
            "AND status = 'open' AND (%(target)s IS NULL OR target_id = %(target)s) "
            "LIMIT 1",
            {"user": user_id, "kind": kind.value, "target": target_id},
        )

        return value is not None

    async def list_by_user(self, user_id: int, kind: FlagKind) -> list[UserFlag]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM user_flags WHERE user_id = %(user)s "
            "AND kind = %(kind)s ORDER BY id DESC",
            {"user": user_id, "kind": kind.value},
        )

        return [UserFlag.model_validate(row) for row in rows]

    async def list_page(
        self,
        *,
        status: FlagStatus | None,
        kind: FlagKind | None,
        user_id: int | None,
        page: int,
        size: int,
    ) -> list[UserFlag]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM user_flags "
            "WHERE (%(status)s IS NULL OR status = %(status)s) "
            "AND (%(kind)s IS NULL OR kind = %(kind)s) "
            "AND (%(user)s IS NULL OR user_id = %(user)s) "
            "ORDER BY id DESC LIMIT %(limit)s OFFSET %(offset)s",
            {
                "status": None if status is None else status.value,
                "kind": None if kind is None else kind.value,
                "user": user_id,
                "limit": size,
                "offset": offset(page, size),
            },
        )

        return [UserFlag.model_validate(row) for row in rows]

    async def count_page(
        self,
        *,
        status: FlagStatus | None,
        kind: FlagKind | None,
        user_id: int | None,
    ) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM user_flags "
            "WHERE (%(status)s IS NULL OR status = %(status)s) "
            "AND (%(kind)s IS NULL OR kind = %(kind)s) "
            "AND (%(user)s IS NULL OR user_id = %(user)s)",
            {
                "status": None if status is None else status.value,
                "kind": None if kind is None else kind.value,
                "user": user_id,
            },
        )

        return count

    async def count_open_by_user(self, user_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM user_flags WHERE user_id = %(id)s "
            "AND status = 'open'",
            {"id": user_id},
        )

        return count

    async def count_open_by_users(self, user_ids: list[int]) -> dict[int, int]:
        if not user_ids:
            return {}

        sql, values = placeholders(user_ids, "u")

        rows = await self._mysql.fetch_all(
            "SELECT user_id, COUNT(*) AS open FROM user_flags "
            f"WHERE user_id IN ({sql}) AND status = 'open' GROUP BY user_id",
            values,
        )

        return {int(row["user_id"]): int(row["open"]) for row in rows}

    async def resolve(
        self, flag_id: int, status: FlagStatus, *, resolved_by_user_id: int
    ) -> None:
        await self._mysql.execute(
            "UPDATE user_flags SET status = %(status)s, resolved_at = %(now)s, "
            "resolved_by_user_id = %(by)s WHERE id = %(id)s",
            {
                "id": flag_id,
                "status": status.value,
                "now": clock.now(),
                "by": resolved_by_user_id,
            },
        )

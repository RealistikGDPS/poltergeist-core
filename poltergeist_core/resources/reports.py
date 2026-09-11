from datetime import datetime
from datetime import timedelta

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import offset
from poltergeist_core.utilities import clock

_RECENT = timedelta(hours=1)


class ReportRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def create(self, level_id: int, user_id: int | None, ip: bytes | None) -> int:
        result = await self._mysql.execute(
            "INSERT INTO level_reports (level_id, user_id, ip) "
            "VALUES (%(level)s, %(user)s, %(ip)s)",
            {"level": level_id, "user": user_id, "ip": ip},
        )

        return result.last_row_id

    async def exists_open(self, level_id: int, user_id: int) -> bool:
        value = await self._mysql.fetch_val(
            "SELECT 1 FROM level_reports WHERE level_id = %(level)s "
            "AND user_id = %(user)s AND resolved_at IS NULL LIMIT 1",
            {"level": level_id, "user": user_id},
        )

        return value is not None

    async def count_recent_by_ip(self, ip: bytes) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM level_reports WHERE ip = %(ip)s "
            "AND created_at >= %(since)s",
            {"ip": ip, "since": clock.now() - _RECENT},
        )

        return count

    async def list_open_level_ids(self, page: int, size: int) -> list[tuple[int, int]]:
        """`(level_id, open reports)` pairs, most reported first."""

        rows = await self._mysql.fetch_all(
            "SELECT level_id, COUNT(*) AS reports FROM level_reports "
            "WHERE resolved_at IS NULL GROUP BY level_id ORDER BY reports DESC, "
            "level_id "
            "LIMIT %(limit)s OFFSET %(offset)s",
            {"limit": size, "offset": offset(page, size)},
        )

        return [(int(row["level_id"]), int(row["reports"])) for row in rows]

    async def count_open_levels(self) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(DISTINCT level_id) FROM level_reports WHERE resolved_at IS "
            "NULL"
        )

        return count

    async def resolve_for_level(
        self, level_id: int, resolved_by_user_id: int | None
    ) -> None:
        now: datetime = clock.now()

        await self._mysql.execute(
            "UPDATE level_reports SET resolved_at = %(now)s, "
            "resolved_by_user_id = %(by)s WHERE level_id = %(id)s "
            "AND resolved_at IS NULL",
            {"id": level_id, "by": resolved_by_user_id, "now": now},
        )

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.utilities import clock


class BlockRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def is_blocked(self, user_id: int, blocked_user_id: int) -> bool:
        value = await self._mysql.fetch_val(
            "SELECT 1 FROM user_blocks WHERE user_id = %(id)s "
            "AND blocked_user_id = %(blocked)s AND deleted_at IS NULL",
            {"id": user_id, "blocked": blocked_user_id},
        )

        return value is not None

    async def is_blocked_either_way(self, user_id: int, other_user_id: int) -> bool:
        value = await self._mysql.fetch_val(
            "SELECT 1 FROM user_blocks WHERE deleted_at IS NULL AND "
            "((user_id = %(a)s AND blocked_user_id = %(b)s) OR "
            "(user_id = %(b)s AND blocked_user_id = %(a)s)) LIMIT 1",
            {"a": user_id, "b": other_user_id},
        )

        return value is not None

    async def list_blocked_ids(self, user_id: int) -> list[int]:
        rows = await self._mysql.fetch_all(
            "SELECT blocked_user_id FROM user_blocks WHERE user_id = %(id)s "
            "AND deleted_at IS NULL ORDER BY created_at DESC",
            {"id": user_id},
        )

        return [int(row["blocked_user_id"]) for row in rows]

    async def create(self, user_id: int, blocked_user_id: int) -> None:
        await self._mysql.execute(
            "INSERT INTO user_blocks (user_id, blocked_user_id, created_at) "
            "VALUES (%(id)s, %(blocked)s, %(now)s) ON DUPLICATE KEY UPDATE "
            "created_at = VALUES(created_at), deleted_at = NULL",
            {"id": user_id, "blocked": blocked_user_id, "now": clock.now()},
        )

    async def delete(self, user_id: int, blocked_user_id: int) -> None:
        await self._mysql.execute(
            "UPDATE user_blocks SET deleted_at = %(now)s WHERE user_id = %(id)s "
            "AND blocked_user_id = %(blocked)s AND deleted_at IS NULL",
            {"id": user_id, "blocked": blocked_user_id, "now": clock.now()},
        )

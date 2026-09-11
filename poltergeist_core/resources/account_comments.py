from datetime import datetime

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset
from poltergeist_core.utilities import clock

_COLUMNS = "id, user_id, content, likes, created_at"


class AccountComment(Model):
    id: int
    user_id: int
    content: str
    likes: int
    created_at: datetime


class AccountCommentRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_id(self, comment_id: int) -> AccountComment | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM account_comments WHERE id = %(id)s "
            "AND deleted_at IS NULL",
            {"id": comment_id},
        )

        return None if row is None else AccountComment.model_validate(row)

    async def create(self, user_id: int, content: str) -> int:
        result = await self._mysql.execute(
            "INSERT INTO account_comments (user_id, content) "
            "VALUES (%(id)s, %(content)s)",
            {"id": user_id, "content": content},
        )

        return result.last_row_id

    async def soft_delete(self, comment_id: int) -> None:
        await self._mysql.execute(
            "UPDATE account_comments SET deleted_at = %(now)s WHERE id = %(id)s",
            {"id": comment_id, "now": clock.now()},
        )

    async def list_by_user(
        self, user_id: int, page: int, size: int
    ) -> list[AccountComment]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM account_comments WHERE user_id = %(id)s "
            "AND deleted_at IS NULL ORDER BY created_at DESC "
            "LIMIT %(limit)s OFFSET %(offset)s",
            {"id": user_id, "limit": size, "offset": offset(page, size)},
        )

        return [AccountComment.model_validate(row) for row in rows]

    async def count_by_user(self, user_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM account_comments WHERE user_id = %(id)s "
            "AND deleted_at IS NULL",
            {"id": user_id},
        )

        return count

    async def list_recent(
        self, *, query: str, user_id: int | None, page: int, size: int
    ) -> list[AccountComment]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM account_comments WHERE deleted_at IS NULL "
            "AND content LIKE %(pattern)s "
            "AND (%(user)s IS NULL OR user_id = %(user)s) "
            "ORDER BY id DESC LIMIT %(limit)s OFFSET %(offset)s",
            {
                "pattern": f"%{query}%",
                "user": user_id,
                "limit": size,
                "offset": offset(page, size),
            },
        )

        return [AccountComment.model_validate(row) for row in rows]

    async def count_recent(self, *, query: str, user_id: int | None) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM account_comments WHERE deleted_at IS NULL "
            "AND content LIKE %(pattern)s AND (%(user)s IS NULL OR user_id = %(user)s)",
            {"pattern": f"%{query}%", "user": user_id},
        )

        return count

    async def add_likes(self, comment_id: int, delta: int) -> None:
        await self._mysql.execute(
            "UPDATE account_comments SET likes = likes + %(delta)s WHERE id = %(id)s",
            {"id": comment_id, "delta": delta},
        )

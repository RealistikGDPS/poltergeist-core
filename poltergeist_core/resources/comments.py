from datetime import datetime

from gdformat.enums import CommentMode

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset
from poltergeist_core.utilities import clock

_COLUMNS = (
    "id, user_id, level_id, list_id, content, percent, likes, is_spam, created_at"
)


class Comment(Model):
    id: int
    user_id: int
    level_id: int | None
    list_id: int | None
    content: str
    percent: int
    likes: int
    is_spam: bool
    created_at: datetime


def _order_sql(mode: CommentMode) -> str:
    match mode:
        case CommentMode.RECENT:
            return "created_at DESC, id DESC"
        case CommentMode.MOST_LIKED:
            return "likes DESC, id DESC"


class CommentRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_id(self, comment_id: int) -> Comment | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM comments WHERE id = %(id)s AND deleted_at IS NULL",
            {"id": comment_id},
        )

        return None if row is None else Comment.model_validate(row)

    async def create(
        self,
        *,
        user_id: int,
        level_id: int | None,
        list_id: int | None,
        content: str,
        percent: int,
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO comments (user_id, level_id, list_id, content, percent) "
            "VALUES (%(user)s, %(level)s, %(list)s, %(content)s, %(percent)s)",
            {
                "user": user_id,
                "level": level_id,
                "list": list_id,
                "content": content,
                "percent": percent,
            },
        )

        return result.last_row_id

    async def soft_delete(self, comment_id: int) -> None:
        await self._mysql.execute(
            "UPDATE comments SET deleted_at = %(now)s WHERE id = %(id)s",
            {"id": comment_id, "now": clock.now()},
        )

    async def list_by_level(
        self, level_id: int, mode: CommentMode, page: int, size: int
    ) -> list[Comment]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM comments WHERE level_id = %(id)s "
            f"AND deleted_at IS NULL ORDER BY {_order_sql(mode)} "
            "LIMIT %(limit)s OFFSET %(offset)s",
            {"id": level_id, "limit": size, "offset": offset(page, size)},
        )

        return [Comment.model_validate(row) for row in rows]

    async def count_by_level(self, level_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM comments WHERE level_id = %(id)s AND deleted_at IS "
            "NULL",
            {"id": level_id},
        )

        return count

    async def list_by_list(
        self, list_id: int, mode: CommentMode, page: int, size: int
    ) -> list[Comment]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM comments WHERE list_id = %(id)s "
            f"AND deleted_at IS NULL ORDER BY {_order_sql(mode)} "
            "LIMIT %(limit)s OFFSET %(offset)s",
            {"id": list_id, "limit": size, "offset": offset(page, size)},
        )

        return [Comment.model_validate(row) for row in rows]

    async def count_by_list(self, list_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM comments WHERE list_id = %(id)s AND deleted_at IS "
            "NULL",
            {"id": list_id},
        )

        return count

    async def list_by_user(
        self, user_id: int, mode: CommentMode, page: int, size: int
    ) -> list[Comment]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM comments WHERE user_id = %(id)s "
            f"AND deleted_at IS NULL ORDER BY {_order_sql(mode)} "
            "LIMIT %(limit)s OFFSET %(offset)s",
            {"id": user_id, "limit": size, "offset": offset(page, size)},
        )

        return [Comment.model_validate(row) for row in rows]

    async def count_by_user(self, user_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM comments WHERE user_id = %(id)s AND deleted_at IS "
            "NULL",
            {"id": user_id},
        )

        return count

    async def list_recent(
        self, *, query: str, user_id: int | None, page: int, size: int
    ) -> list[Comment]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM comments WHERE deleted_at IS NULL "
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

        return [Comment.model_validate(row) for row in rows]

    async def count_recent(self, *, query: str, user_id: int | None) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM comments WHERE deleted_at IS NULL "
            "AND content LIKE %(pattern)s AND (%(user)s IS NULL OR user_id = %(user)s)",
            {"pattern": f"%{query}%", "user": user_id},
        )

        return count

    async def add_likes(self, comment_id: int, delta: int) -> None:
        await self._mysql.execute(
            "UPDATE comments SET likes = likes + %(delta)s WHERE id = %(id)s",
            {"id": comment_id, "delta": delta},
        )

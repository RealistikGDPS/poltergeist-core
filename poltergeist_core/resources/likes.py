from enum import StrEnum

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model


class LikeTarget(StrEnum):
    LEVEL = "level"
    COMMENT = "comment"
    ACCOUNT_COMMENT = "account_comment"
    LEVEL_LIST = "level_list"


class Like(Model):
    id: int
    user_id: int
    target_type: LikeTarget
    target_id: int
    is_like: bool


class LikeRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find(
        self, user_id: int, target_type: LikeTarget, target_id: int
    ) -> Like | None:
        row = await self._mysql.fetch_one(
            "SELECT id, user_id, target_type, target_id, is_like FROM likes "
            "WHERE user_id = %(user)s AND target_type = %(type)s "
            "AND target_id = %(target)s",
            {"user": user_id, "type": target_type.value, "target": target_id},
        )

        return None if row is None else Like.model_validate(row)

    async def create(
        self, user_id: int, target_type: LikeTarget, target_id: int, *, is_like: bool
    ) -> None:
        await self._mysql.execute(
            "INSERT INTO likes (user_id, target_type, target_id, is_like) "
            "VALUES (%(user)s, %(type)s, %(target)s, %(like)s)",
            {
                "user": user_id,
                "type": target_type.value,
                "target": target_id,
                "like": is_like,
            },
        )

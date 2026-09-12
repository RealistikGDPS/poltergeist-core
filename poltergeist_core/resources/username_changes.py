from datetime import datetime

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model

_COLUMNS = "id, user_id, changed_by_user_id, old_username, new_username, changed_at"


class UsernameChange(Model):
    id: int
    user_id: int
    changed_by_user_id: int
    old_username: str
    new_username: str
    changed_at: datetime


class UsernameChangeRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def create(
        self,
        user_id: int,
        old_username: str,
        new_username: str,
        *,
        changed_by_user_id: int,
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO username_changes "
            "(user_id, changed_by_user_id, old_username, new_username) "
            "VALUES (%(user_id)s, %(actor_id)s, %(old)s, %(new)s)",
            {
                "user_id": user_id,
                "actor_id": changed_by_user_id,
                "old": old_username,
                "new": new_username,
            },
        )

        return result.last_row_id

    async def last_self_change_at(self, user_id: int) -> datetime | None:
        """Renames made by the user themselves; a moderator's rename does not
        count against them."""

        moment: datetime | None = await self._mysql.fetch_val(
            "SELECT MAX(changed_at) FROM username_changes "
            "WHERE user_id = %(id)s AND changed_by_user_id = %(id)s",
            {"id": user_id},
        )

        return moment

    async def list_by_user(self, user_id: int) -> list[UsernameChange]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM username_changes WHERE user_id = %(id)s "
            "ORDER BY changed_at DESC",
            {"id": user_id},
        )

        return [UsernameChange.model_validate(row) for row in rows]

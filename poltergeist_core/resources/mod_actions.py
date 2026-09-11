import json
from datetime import datetime
from enum import StrEnum

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import JsonObject
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset


class ModTarget(StrEnum):
    USER = "user"
    LEVEL = "level"
    LEVEL_LIST = "level_list"
    COMMENT = "comment"
    ACCOUNT_COMMENT = "account_comment"
    SONG = "song"
    TIMELY_LEVEL = "timely_level"
    MAP_PACK = "map_pack"
    GAUNTLET = "gauntlet"
    QUEST = "quest"
    SECRET_REWARD = "secret_reward"
    ROLE = "role"


class ModAction(Model):
    id: int
    user_id: int
    action: str
    target_type: ModTarget
    target_id: int
    details: JsonObject
    created_at: datetime


class ModActionRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def create(
        self,
        user_id: int,
        action: str,
        target_type: ModTarget,
        target_id: int,
        details: dict[str, object] | None = None,
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO mod_actions (user_id, action, target_type, target_id, "
            "details) "
            "VALUES (%(user)s, %(action)s, %(type)s, %(target)s, %(details)s)",
            {
                "user": user_id,
                "action": action,
                "type": target_type.value,
                "target": target_id,
                "details": None if details is None else json.dumps(details),
            },
        )

        return result.last_row_id

    async def list_recent(
        self,
        *,
        user_id: int | None,
        target_type: ModTarget | None,
        target_id: int | None,
        page: int,
        size: int,
    ) -> list[ModAction]:
        rows = await self._mysql.fetch_all(
            "SELECT id, user_id, action, target_type, target_id, details, created_at "
            "FROM mod_actions WHERE (%(user)s IS NULL OR user_id = %(user)s) "
            "AND (%(type)s IS NULL OR target_type = %(type)s) "
            "AND (%(target)s IS NULL OR target_id = %(target)s) "
            "ORDER BY id DESC LIMIT %(limit)s OFFSET %(offset)s",
            {
                "user": user_id,
                "type": None if target_type is None else target_type.value,
                "target": target_id,
                "limit": size,
                "offset": offset(page, size),
            },
        )

        return [ModAction.model_validate(row) for row in rows]

    async def count_recent(
        self,
        *,
        user_id: int | None,
        target_type: ModTarget | None,
        target_id: int | None,
    ) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM mod_actions WHERE (%(user)s IS NULL OR user_id = "
            "%(user)s) "
            "AND (%(type)s IS NULL OR target_type = %(type)s) "
            "AND (%(target)s IS NULL OR target_id = %(target)s)",
            {
                "user": user_id,
                "type": None if target_type is None else target_type.value,
                "target": target_id,
            },
        )

        return count

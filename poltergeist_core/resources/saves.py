from datetime import datetime

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model


class UserSave(Model):
    user_id: int
    game_version: int
    binary_version: int
    game_manager_bytes: int
    local_levels_bytes: int
    saved_at: datetime


class SaveRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_user_id(self, user_id: int) -> UserSave | None:
        row = await self._mysql.fetch_one(
            "SELECT user_id, game_version, binary_version, game_manager_bytes, "
            "local_levels_bytes, saved_at FROM user_saves WHERE user_id = %(id)s",
            {"id": user_id},
        )

        return None if row is None else UserSave.model_validate(row)

    async def upsert(
        self,
        user_id: int,
        *,
        game_version: int,
        binary_version: int,
        game_manager_bytes: int,
        local_levels_bytes: int,
    ) -> None:
        await self._mysql.execute(
            "INSERT INTO user_saves (user_id, game_version, binary_version, "
            "game_manager_bytes, local_levels_bytes) VALUES (%(id)s, %(game)s, "
            "%(binary)s, %(manager)s, %(levels)s) ON DUPLICATE KEY UPDATE "
            "game_version = VALUES(game_version), "
            "binary_version = VALUES(binary_version), "
            "game_manager_bytes = VALUES(game_manager_bytes), "
            "local_levels_bytes = VALUES(local_levels_bytes)",
            {
                "id": user_id,
                "game": game_version,
                "binary": binary_version,
                "manager": game_manager_bytes,
                "levels": local_levels_bytes,
            },
        )

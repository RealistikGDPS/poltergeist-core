from datetime import datetime

from gdformat.enums import DemonDifficulty
from gdformat.enums import SendFeature

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset
from poltergeist_core.utilities import clock

_COLUMNS = "id, level_id, user_id, stars, feature, demon_difficulty, created_at"


class LevelSuggestion(Model):
    id: int
    level_id: int
    user_id: int
    stars: int | None
    feature: SendFeature | None
    demon_difficulty: DemonDifficulty | None
    created_at: datetime


class SuggestionRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def create(
        self,
        level_id: int,
        user_id: int,
        *,
        stars: int | None,
        feature: SendFeature | None,
        demon_difficulty: DemonDifficulty | None,
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO level_suggestions (level_id, user_id, stars, feature, "
            "demon_difficulty) VALUES (%(level)s, %(user)s, %(stars)s, %(feature)s, "
            "%(demon)s)",
            {
                "level": level_id,
                "user": user_id,
                "stars": stars,
                "feature": None if feature is None else int(feature),
                "demon": None if demon_difficulty is None else int(demon_difficulty),
            },
        )

        return result.last_row_id

    async def find_pending(self, level_id: int) -> LevelSuggestion | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM level_suggestions WHERE level_id = %(id)s "
            "AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1",
            {"id": level_id},
        )

        return None if row is None else LevelSuggestion.model_validate(row)

    async def list_pending(self, page: int, size: int) -> list[LevelSuggestion]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM level_suggestions WHERE deleted_at IS NULL "
            "ORDER BY created_at DESC LIMIT %(limit)s OFFSET %(offset)s",
            {"limit": size, "offset": offset(page, size)},
        )

        return [LevelSuggestion.model_validate(row) for row in rows]

    async def count_pending(self) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM level_suggestions WHERE deleted_at IS NULL"
        )

        return count

    async def resolve_for_level(self, level_id: int) -> None:
        await self._mysql.execute(
            "UPDATE level_suggestions SET deleted_at = %(now)s WHERE level_id = %(id)s "
            "AND deleted_at IS NULL",
            {"id": level_id, "now": clock.now()},
        )

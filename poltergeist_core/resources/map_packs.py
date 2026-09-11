from gdformat.enums import MapPackDifficulty

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset
from poltergeist_core.resources._common import placeholders
from poltergeist_core.utilities import clock

_COLUMNS = "id, name, stars, coins, difficulty, text_colour, bar_colour"


class MapPack(Model):
    id: int
    name: str
    stars: int
    coins: int
    difficulty: MapPackDifficulty
    text_colour: int
    bar_colour: int


class MapPackRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_id(self, pack_id: int) -> MapPack | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM map_packs WHERE id = %(id)s AND deleted_at IS "
            f"NULL",
            {"id": pack_id},
        )

        return None if row is None else MapPack.model_validate(row)

    async def list_page(self, page: int, size: int) -> list[MapPack]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM map_packs WHERE deleted_at IS NULL "
            "ORDER BY id LIMIT %(limit)s OFFSET %(offset)s",
            {"limit": size, "offset": offset(page, size)},
        )

        return [MapPack.model_validate(row) for row in rows]

    async def list_all(self) -> list[MapPack]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM map_packs WHERE deleted_at IS NULL ORDER BY id"
        )

        return [MapPack.model_validate(row) for row in rows]

    async def count(self) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM map_packs WHERE deleted_at IS NULL"
        )

        return count

    async def list_level_ids_many(self, pack_ids: list[int]) -> dict[int, list[int]]:
        if not pack_ids:
            return {}

        sql, values = placeholders(pack_ids, "id")

        rows = await self._mysql.fetch_all(
            f"SELECT map_pack_id, level_id FROM map_pack_levels WHERE map_pack_id IN "
            f"({sql}) "
            "ORDER BY map_pack_id, position",
            values,
        )

        levels: dict[int, list[int]] = {pack_id: [] for pack_id in pack_ids}

        for row in rows:
            levels[int(row["map_pack_id"])].append(int(row["level_id"]))

        return levels

    async def create(
        self,
        *,
        name: str,
        stars: int,
        coins: int,
        difficulty: MapPackDifficulty,
        text_colour: int,
        bar_colour: int,
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO map_packs (name, stars, coins, difficulty, text_colour, "
            "bar_colour) VALUES (%(name)s, %(stars)s, %(coins)s, %(difficulty)s, "
            "%(text)s, %(bar)s)",
            {
                "name": name,
                "stars": stars,
                "coins": coins,
                "difficulty": int(difficulty),
                "text": text_colour,
                "bar": bar_colour,
            },
        )

        return result.last_row_id

    async def replace_levels(self, pack_id: int, level_ids: list[int]) -> None:
        await self._mysql.execute(
            "DELETE FROM map_pack_levels WHERE map_pack_id = %(id)s",
            {"id": pack_id},
        )

        for position, level_id in enumerate(level_ids):
            await self._mysql.execute(
                "INSERT INTO map_pack_levels (map_pack_id, position, level_id) "
                "VALUES (%(pack)s, %(position)s, %(level)s)",
                {"pack": pack_id, "position": position, "level": level_id},
            )

    async def soft_delete(self, pack_id: int) -> None:
        await self._mysql.execute(
            "UPDATE map_packs SET deleted_at = %(now)s WHERE id = %(id)s",
            {"id": pack_id, "now": clock.now()},
        )

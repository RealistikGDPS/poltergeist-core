from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.utilities import clock


class Gauntlet(Model):
    id: int
    level_ids: list[int]


class GauntletRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_id(self, gauntlet_id: int) -> Gauntlet | None:
        exists = await self._mysql.fetch_val(
            "SELECT 1 FROM gauntlets WHERE id = %(id)s AND deleted_at IS NULL",
            {"id": gauntlet_id},
        )

        if exists is None:
            return None

        rows = await self._mysql.fetch_all(
            "SELECT level_id FROM gauntlet_levels WHERE gauntlet_id = %(id)s "
            "ORDER BY position",
            {"id": gauntlet_id},
        )

        return Gauntlet(
            id=gauntlet_id, level_ids=[int(row["level_id"]) for row in rows]
        )

    async def list_all(self) -> list[Gauntlet]:
        rows = await self._mysql.fetch_all(
            "SELECT g.id, gl.level_id FROM gauntlets g "
            "JOIN gauntlet_levels gl ON gl.gauntlet_id = g.id "
            "WHERE g.deleted_at IS NULL ORDER BY g.id, gl.position"
        )

        grouped: dict[int, list[int]] = {}

        for row in rows:
            grouped.setdefault(int(row["id"]), []).append(int(row["level_id"]))

        return [
            Gauntlet(id=gauntlet_id, level_ids=level_ids)
            for gauntlet_id, level_ids in grouped.items()
        ]

    async def upsert(self, gauntlet_id: int, level_ids: list[int]) -> None:
        await self._mysql.execute(
            "INSERT INTO gauntlets (id) VALUES (%(id)s) "
            "ON DUPLICATE KEY UPDATE deleted_at = NULL",
            {"id": gauntlet_id},
        )
        await self._mysql.execute(
            "DELETE FROM gauntlet_levels WHERE gauntlet_id = %(id)s",
            {"id": gauntlet_id},
        )

        for position, level_id in enumerate(level_ids, start=1):
            await self._mysql.execute(
                "INSERT INTO gauntlet_levels (gauntlet_id, position, level_id) "
                "VALUES (%(gauntlet)s, %(position)s, %(level)s)",
                {"gauntlet": gauntlet_id, "position": position, "level": level_id},
            )

    async def soft_delete(self, gauntlet_id: int) -> None:
        await self._mysql.execute(
            "UPDATE gauntlets SET deleted_at = %(now)s WHERE id = %(id)s",
            {"id": gauntlet_id, "now": clock.now()},
        )

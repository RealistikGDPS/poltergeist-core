from poltergeist_core.adapters.mysql import ImplementsMySQL


class StarVoteRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def upsert(self, level_id: int, user_id: int, stars: int) -> None:
        await self._mysql.execute(
            "INSERT INTO level_star_votes (level_id, user_id, stars) "
            "VALUES (%(level)s, %(user)s, %(stars)s) "
            "ON DUPLICATE KEY UPDATE stars = VALUES(stars)",
            {"level": level_id, "user": user_id, "stars": stars},
        )

    async def average(self, level_id: int) -> float | None:
        value: float | None = await self._mysql.fetch_val(
            "SELECT AVG(stars) FROM level_star_votes WHERE level_id = %(id)s",
            {"id": level_id},
        )

        return None if value is None else float(value)

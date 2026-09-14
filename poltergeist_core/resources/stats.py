from gdformat.enums import IconType

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import placeholders
from poltergeist_core.resources.stats_history import StatsSnapshot

_COLUMNS = (
    "user_id, stars, moons, demons, diamonds, secret_coins, user_coins, "
    "creator_points, icon_type, colour1, colour2, colour3, glow, icon_cube, "
    "icon_ship, icon_ball, icon_ufo, icon_wave, icon_robot, icon_spider, "
    "icon_swing, icon_jetpack, icon_explosion, demons_easy, demons_medium, "
    "demons_hard, demons_insane, demons_extreme, demons_easy_platformer, "
    "demons_medium_platformer, demons_hard_platformer, demons_insane_platformer, "
    "demons_extreme_platformer, demons_weekly, demons_gauntlet, demons_event, "
    "classic_auto, classic_easy, classic_normal, classic_hard, classic_harder, "
    "classic_insane, classic_daily, classic_gauntlet, platformer_auto, "
    "platformer_easy, platformer_normal, platformer_hard, platformer_harder, "
    "platformer_insane, platformer_event"
)


class UserStats(Model):
    user_id: int
    stars: int
    moons: int
    demons: int
    diamonds: int
    secret_coins: int
    user_coins: int
    creator_points: int
    icon_type: IconType
    colour1: int
    colour2: int
    colour3: int
    glow: bool
    icon_cube: int
    icon_ship: int
    icon_ball: int
    icon_ufo: int
    icon_wave: int
    icon_robot: int
    icon_spider: int
    icon_swing: int
    icon_jetpack: int
    icon_explosion: int
    demons_easy: int
    demons_medium: int
    demons_hard: int
    demons_insane: int
    demons_extreme: int
    demons_easy_platformer: int
    demons_medium_platformer: int
    demons_hard_platformer: int
    demons_insane_platformer: int
    demons_extreme_platformer: int
    demons_weekly: int
    demons_gauntlet: int
    demons_event: int
    classic_auto: int
    classic_easy: int
    classic_normal: int
    classic_hard: int
    classic_harder: int
    classic_insane: int
    classic_daily: int
    classic_gauntlet: int
    platformer_auto: int
    platformer_easy: int
    platformer_normal: int
    platformer_hard: int
    platformer_harder: int
    platformer_insane: int
    platformer_event: int

    @property
    def selected_icon(self) -> int:
        match self.icon_type:
            case IconType.CUBE:
                return self.icon_cube
            case IconType.SHIP:
                return self.icon_ship
            case IconType.BALL:
                return self.icon_ball
            case IconType.UFO:
                return self.icon_ufo
            case IconType.WAVE:
                return self.icon_wave
            case IconType.ROBOT:
                return self.icon_robot
            case IconType.SPIDER:
                return self.icon_spider
            case IconType.SWING:
                return self.icon_swing
            case IconType.JETPACK:
                return self.icon_jetpack


class StatsUpdate(Model):
    """Every column a client may change through updateGJUserScore."""

    stars: int
    moons: int
    demons: int
    diamonds: int
    secret_coins: int
    user_coins: int
    icon_type: IconType
    colour1: int
    colour2: int
    colour3: int
    glow: bool
    icon_cube: int
    icon_ship: int
    icon_ball: int
    icon_ufo: int
    icon_wave: int
    icon_robot: int
    icon_spider: int
    icon_swing: int
    icon_jetpack: int
    icon_explosion: int
    demons_easy: int
    demons_medium: int
    demons_hard: int
    demons_insane: int
    demons_extreme: int
    demons_easy_platformer: int
    demons_medium_platformer: int
    demons_hard_platformer: int
    demons_insane_platformer: int
    demons_extreme_platformer: int
    demons_weekly: int
    demons_gauntlet: int
    demons_event: int
    classic_auto: int
    classic_easy: int
    classic_normal: int
    classic_hard: int
    classic_harder: int
    classic_insane: int
    classic_daily: int
    classic_gauntlet: int
    platformer_auto: int
    platformer_easy: int
    platformer_normal: int
    platformer_hard: int
    platformer_harder: int
    platformer_insane: int
    platformer_event: int


class RankedStats(Model):
    user_id: int
    stars: int
    moons: int
    demons: int
    user_coins: int
    creator_points: int


class StatsRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_user_id(self, user_id: int) -> UserStats | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM user_stats WHERE user_id = %(id)s",
            {"id": user_id},
        )

        return None if row is None else UserStats.model_validate(row)

    async def find_many_by_user_ids(self, user_ids: list[int]) -> list[UserStats]:
        if not user_ids:
            return []

        sql, values = placeholders(user_ids, "id")

        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM user_stats WHERE user_id IN ({sql})",
            values,
        )

        return [UserStats.model_validate(row) for row in rows]

    async def create(self, user_id: int) -> None:
        await self._mysql.execute(
            "INSERT INTO user_stats (user_id) VALUES (%(id)s)",
            {"id": user_id},
        )

    async def update(self, user_id: int, update: StatsUpdate) -> None:
        values = update.model_dump()
        assignments = ", ".join(f"{column} = %({column})s" for column in values)
        values["icon_type"] = int(update.icon_type)

        await self._mysql.execute(
            f"UPDATE user_stats SET {assignments} WHERE user_id = %(user_id)s",
            {**values, "user_id": user_id},
        )

    async def restore(self, user_id: int, snapshot: StatsSnapshot) -> None:
        values = snapshot.model_dump()
        assignments = ", ".join(f"{column} = %({column})s" for column in values)

        await self._mysql.execute(
            f"UPDATE user_stats SET {assignments} WHERE user_id = %(user_id)s",
            {**values, "user_id": user_id},
        )

    async def update_creator_points(self, user_id: int, creator_points: int) -> None:
        await self._mysql.execute(
            "UPDATE user_stats SET creator_points = %(points)s WHERE user_id = %(id)s",
            {"id": user_id, "points": creator_points},
        )

    async def list_ranked_after(self, user_id: int, limit: int) -> list[RankedStats]:
        rows = await self._mysql.fetch_all(
            "SELECT user_id, stars, moons, demons, user_coins, creator_points "
            "FROM user_stats WHERE user_id > %(after)s ORDER BY user_id "
            "LIMIT %(limit)s",
            {"after": user_id, "limit": limit},
        )

        return [RankedStats.model_validate(row) for row in rows]

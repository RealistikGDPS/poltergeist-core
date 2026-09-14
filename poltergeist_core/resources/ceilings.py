from gdformat.enums import Difficulty
from gdformat.enums import Length

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.adapters.redis import RedisClient
from poltergeist_core.resources._common import Model

_CACHE_KEY = "anticheat:ceilings"
_CACHE_SECONDS = 300


class StatCeilings(Model):
    """The most of each counter the server's own content can have awarded."""

    stars: int
    moons: int
    demons: int
    secret_coins: int
    user_coins: int


class CeilingRepository:
    """Aggregates over every level ever rated, deleted ones included, since a
    player keeps what a removed level once awarded."""

    __slots__ = ("_mysql", "_redis")

    def __init__(self, mysql: ImplementsMySQL, redis: RedisClient) -> None:
        self._mysql = mysql
        self._redis = redis

    async def load(self) -> StatCeilings:
        cached = await self._redis.get(_CACHE_KEY)

        if cached is not None:
            return StatCeilings.model_validate_json(str(cached))

        row = await self._mysql.fetch_one(
            "SELECT "
            "(SELECT COALESCE(SUM(stars), 0) FROM levels WHERE stars > 0 "
            "AND length <> %(platformer)s) "
            "+ (SELECT COALESCE(SUM(stars), 0) FROM map_packs) AS stars, "
            "(SELECT COALESCE(SUM(stars), 0) FROM levels WHERE stars > 0 "
            "AND length = %(platformer)s) AS moons, "
            "(SELECT COUNT(*) FROM levels WHERE stars > 0 "
            "AND difficulty >= %(demon)s) AS demons, "
            "(SELECT COALESCE(SUM(coins), 0) FROM map_packs) AS secret_coins, "
            "(SELECT COALESCE(SUM(coins), 0) FROM levels WHERE stars > 0 "
            "AND coins_verified) AS user_coins",
            {"platformer": int(Length.PLATFORMER), "demon": int(Difficulty.EASY_DEMON)},
        )
        assert row is not None, "An aggregate query always yields one row."
        ceilings = StatCeilings.model_validate(row)
        await self._redis.set(_CACHE_KEY, ceilings.model_dump_json(), ex=_CACHE_SECONDS)

        return ceilings

    async def invalidate(self) -> None:
        await self._redis.delete(_CACHE_KEY)

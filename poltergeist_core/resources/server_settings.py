from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.adapters.redis import RedisClient
from poltergeist_core.resources._common import Model
from poltergeist_core.utilities import clock

_CACHE_KEY = "server_settings"
_CACHE_SECONDS = 30


class ServerSettings(Model):
    """Operator-editable switches read on the request path. A key missing
    from storage takes the default declared here, so adding a setting needs
    no migration."""

    registration_enabled: bool = True
    level_uploads_enabled: bool = True
    song_reupload_enabled: bool = False
    level_reupload_enabled: bool = False
    download_pc_url: str = ""
    download_android_url: str = ""
    # What the official main levels award, added to the rated content when
    # checking a profile against the ceilings. The 22 main levels give 199
    # stars, 66 secret coins and 3 demons; raise these if the client's extra
    # levels are found to add more.
    official_stars: int = 199
    official_moons: int = 0
    official_demons: int = 3
    official_secret_coins: int = 66


class ServerSettingRepository:
    """Cached in Redis for a short while; every write MUST call `invalidate`."""

    __slots__ = ("_mysql", "_redis")

    def __init__(self, mysql: ImplementsMySQL, redis: RedisClient) -> None:
        self._mysql = mysql
        self._redis = redis

    async def load(self) -> ServerSettings:
        cached = await self._redis.get(_CACHE_KEY)

        if cached is not None:
            return ServerSettings.model_validate_json(str(cached))

        rows = await self._mysql.fetch_all("SELECT `key`, value FROM server_settings")
        settings = ServerSettings.model_validate(
            {str(row["key"]): str(row["value"]) for row in rows}
        )
        await self._redis.set(_CACHE_KEY, settings.model_dump_json(), ex=_CACHE_SECONDS)

        return settings

    async def set(self, key: str, value: str, *, updated_by_user_id: int) -> None:
        await self._mysql.execute(
            "INSERT INTO server_settings (`key`, value, updated_by_user_id, "
            "updated_at) VALUES (%(key)s, %(value)s, %(by)s, %(now)s) "
            "ON DUPLICATE KEY UPDATE value = VALUES(value), "
            "updated_by_user_id = VALUES(updated_by_user_id), "
            "updated_at = VALUES(updated_at)",
            {
                "key": key,
                "value": value,
                "by": updated_by_user_id,
                "now": clock.now(),
            },
        )

    async def invalidate(self) -> None:
        await self._redis.delete(_CACHE_KEY)

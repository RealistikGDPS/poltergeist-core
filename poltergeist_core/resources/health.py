from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.adapters.redis import RedisClient
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)


class HealthRepository:
    """Probes the backing stores. The drivers offer no non-raising probe, so
    this is the one place their connection errors are caught."""

    __slots__ = ("_mysql", "_redis")

    def __init__(self, mysql: ImplementsMySQL, redis: RedisClient) -> None:
        self._mysql = mysql
        self._redis = redis

    async def mysql_available(self) -> bool:
        try:
            await self._mysql.fetch_val("SELECT 1")
        except Exception:
            logger.exception("MySQL health probe failed.")

            return False

        return True

    async def redis_facts(self) -> dict[str, str]:
        info = await self._redis.info("memory")
        keys = await self._redis.dbsize()

        return {
            "used_memory_human": str(info.get("used_memory_human", "?")),
            "keys": str(keys),
        }

    async def redis_available(self) -> bool:
        try:
            await self._redis.ping()
        except Exception:
            logger.exception("Redis health probe failed.")

            return False

        return True

import time
from datetime import datetime

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.adapters.redis import RedisClient
from poltergeist_core.resources._common import Model
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_MYSQL_STATUS = ("Uptime", "Threads_connected", "Threads_running", "Questions")


class MySQLFacts(Model):
    version: str
    uptime_seconds: int
    threads_connected: int
    threads_running: int
    max_connections: int
    questions: int


class RedisFacts(Model):
    version: str
    uptime_seconds: int
    used_memory_bytes: int
    used_memory_human: str
    maxmemory_bytes: int
    connected_clients: int
    keys: int
    keyspace_hits: int
    keyspace_misses: int


class MySQLProbe(Model):
    latency_ms: float
    facts: MySQLFacts


class RedisProbe(Model):
    latency_ms: float
    facts: RedisFacts


class StackHealth(Model):
    """A down component is `None`; reporting it is the success path."""

    mysql: MySQLProbe | None
    redis: RedisProbe | None
    checked_at: datetime

    @property
    def healthy(self) -> bool:
        return self.mysql is not None and self.redis is not None


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


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

    async def redis_available(self) -> bool:
        try:
            await self._redis.ping()
        except Exception:
            logger.exception("Redis health probe failed.")

            return False

        return True

    async def _mysql_facts(self) -> MySQLFacts:
        version: str = await self._mysql.fetch_val("SELECT VERSION()")
        names = ", ".join(f"'{name}'" for name in _MYSQL_STATUS)

        status_rows = await self._mysql.fetch_all(
            f"SHOW GLOBAL STATUS WHERE Variable_name IN ({names})"
        )
        status = {str(row["Variable_name"]): str(row["Value"]) for row in status_rows}

        connections = await self._mysql.fetch_one(
            "SHOW GLOBAL VARIABLES LIKE 'max_connections'"
        )

        return MySQLFacts(
            version=version,
            uptime_seconds=int(status.get("Uptime", 0)),
            threads_connected=int(status.get("Threads_connected", 0)),
            threads_running=int(status.get("Threads_running", 0)),
            max_connections=0 if connections is None else int(connections["Value"]),
            questions=int(status.get("Questions", 0)),
        )

    async def probe_mysql(self) -> MySQLProbe | None:
        started = time.perf_counter()

        try:
            facts = await self._mysql_facts()
        except Exception:
            logger.exception("MySQL health probe failed.")

            return None

        return MySQLProbe(latency_ms=_elapsed_ms(started), facts=facts)

    async def _redis_facts(self) -> RedisFacts:
        info = await self._redis.info()
        keys = await self._redis.dbsize()

        return RedisFacts(
            version=str(info.get("redis_version", "")),
            uptime_seconds=int(info.get("uptime_in_seconds", 0)),
            used_memory_bytes=int(info.get("used_memory", 0)),
            used_memory_human=str(info.get("used_memory_human", "")),
            maxmemory_bytes=int(info.get("maxmemory", 0)),
            connected_clients=int(info.get("connected_clients", 0)),
            keys=int(keys),
            keyspace_hits=int(info.get("keyspace_hits", 0)),
            keyspace_misses=int(info.get("keyspace_misses", 0)),
        )

    async def probe_redis(self) -> RedisProbe | None:
        started = time.perf_counter()

        try:
            facts = await self._redis_facts()
        except Exception:
            logger.exception("Redis health probe failed.")

            return None

        return RedisProbe(latency_ms=_elapsed_ms(started), facts=facts)

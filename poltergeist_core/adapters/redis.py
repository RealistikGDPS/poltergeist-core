from redis.asyncio import Redis

from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)


class RedisClient(Redis):
    """The asynchronous Redis client with responses decoded to `str`."""

    def __init__(self, *, host: str, port: int, database: int) -> None:
        super().__init__(host=host, port=port, db=database, decode_responses=True)

    async def initialise(self) -> None:
        await self.ping()
        logger.debug("Redis connection verified.")


def default() -> RedisClient:
    """Builds the production client; `initialise()` still has to be awaited."""

    # Local import keeps this module importable without configuration.
    from poltergeist_core import settings

    return RedisClient(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        database=settings.REDIS_DATABASE,
    )

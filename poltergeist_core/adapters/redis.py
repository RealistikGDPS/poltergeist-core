import beerstream2

from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)


class RedisClient(beerstream2.Client):
    """The event-loop-native Redis client with replies decoded to `str`."""

    async def initialise(self) -> None:
        await self.connect()
        logger.debug("Redis connection verified.")

    async def aclose(self) -> None:
        await self.close()


def default() -> RedisClient:
    """Builds the production client; `initialise()` still has to be awaited."""

    # Local import keeps this module importable without configuration.
    from poltergeist_core import settings

    return RedisClient(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        database=settings.REDIS_DATABASE,
    )

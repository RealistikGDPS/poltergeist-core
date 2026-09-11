from enum import StrEnum
from http import HTTPStatus

from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError


class HealthError(ServiceError, StrEnum):
    MYSQL_UNAVAILABLE = "mysql_unavailable"
    REDIS_UNAVAILABLE = "redis_unavailable"

    def service(self) -> str:
        return "health"

    def status_code(self) -> int:
        return HTTPStatus.SERVICE_UNAVAILABLE


async def check(ctx: AbstractContext) -> HealthError.OnSuccess[None]:
    if not await ctx.health.mysql_available():
        return HealthError.MYSQL_UNAVAILABLE

    if not await ctx.health.redis_available():
        return HealthError.REDIS_UNAVAILABLE

    return None

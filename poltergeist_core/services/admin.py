import hmac
from enum import StrEnum
from http import HTTPStatus

from poltergeist_core import settings
from poltergeist_core.services._common import ServiceError


class AdminError(ServiceError, StrEnum):
    DISABLED = "disabled"
    UNAUTHORISED = "unauthorised"

    def service(self) -> str:
        return "admin"

    def status_code(self) -> int:
        match self:
            case AdminError.DISABLED:
                return HTTPStatus.NOT_FOUND
            case AdminError.UNAUTHORISED:
                return HTTPStatus.UNAUTHORIZED


def verify_key(presented: str | None) -> AdminError.OnSuccess[None]:
    if not settings.APP_ADMIN_API_KEY:
        return AdminError.DISABLED

    if presented is None or not hmac.compare_digest(
        presented, settings.APP_ADMIN_API_KEY
    ):
        return AdminError.UNAUTHORISED

    return None

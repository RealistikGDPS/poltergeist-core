from enum import StrEnum
from http import HTTPStatus

from gdformat.enums import Secret
from gdformat.requests import Client

from poltergeist_core import settings
from poltergeist_core.services._common import ServiceError


class ClientError(ServiceError, StrEnum):
    OUTDATED = "outdated"
    BAD_SECRET = "bad_secret"

    def service(self) -> str:
        return "clients"

    def status_code(self) -> int:
        return HTTPStatus.BAD_REQUEST


def validate(client: Client, expected_secret: Secret) -> ClientError.OnSuccess[None]:
    """Only the latest protocol is served. A client that does not state its
    version is given the benefit of the doubt; one that states an old version
    is refused."""

    if client.secret != expected_secret:
        return ClientError.BAD_SECRET

    if 0 < client.game_version < settings.APP_MIN_GAME_VERSION:
        return ClientError.OUTDATED

    if 0 < client.binary_version < settings.APP_MIN_BINARY_VERSION:
        return ClientError.OUTDATED

    return None

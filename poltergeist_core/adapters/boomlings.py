from enum import StrEnum

import httpx
from gdformat import enums
from gdformat import is_error
from gdformat import objects
from gdformat.objects import Song

from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_SONG_ENDPOINT = "/getGJSongInfo.php"
_NOT_FOUND = "-1"
_NOT_ALLOWED = "-2"
# The official firewall rejects any request carrying a user agent.
_USER_AGENT = ""


class BoomlingsError(StrEnum):
    NOT_FOUND = "not_found"
    NOT_ALLOWED = "not_allowed"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"


type BoomlingsResult[T] = T | BoomlingsError


class BoomlingsClient:
    """A client for the official Geometry Dash servers."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        proxy_url: str | None,
    ) -> None:
        # Egress is explicit: ambient proxy variables must not redirect it.
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout_seconds,
            proxy=proxy_url,
            trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, endpoint: str, data: dict[str, str]) -> BoomlingsResult[str]:
        try:
            response = await self._client.post(endpoint, data=data)
        except httpx.HTTPError:
            logger.warning(
                "The official servers could not be reached.",
                extra={"endpoint": endpoint},
            )

            return BoomlingsError.UNAVAILABLE

        if response.status_code >= 500:
            logger.warning(
                "The official servers returned a server error.",
                extra={"endpoint": endpoint, "status_code": response.status_code},
            )

            return BoomlingsError.UNAVAILABLE

        if response.status_code != 200:
            return BoomlingsError.UNAVAILABLE

        return response.text.strip()

    async def fetch_song(self, song_id: int) -> BoomlingsResult[Song]:
        content = await self._post(
            _SONG_ENDPOINT,
            {"songID": str(song_id), "secret": enums.Secret.COMMON},
        )

        if isinstance(content, BoomlingsError):
            return content

        if content == _NOT_FOUND:
            return BoomlingsError.NOT_FOUND

        if content == _NOT_ALLOWED:
            return BoomlingsError.NOT_ALLOWED

        song = objects.parse_song(content)

        if is_error(song):
            logger.warning(
                "The official servers returned an unparseable song.",
                extra={"song_id": song_id, "error": song.value},
            )

            return BoomlingsError.MALFORMED

        return song


def default() -> BoomlingsClient:
    # Local import keeps this module importable without configuration.
    from poltergeist_core import settings

    return BoomlingsClient(
        base_url=settings.BOOMLINGS_URL,
        timeout_seconds=settings.BOOMLINGS_TIMEOUT_SECONDS,
        proxy_url=settings.BOOMLINGS_PROXY_URL or None,
    )

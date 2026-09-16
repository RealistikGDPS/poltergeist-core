from enum import StrEnum

import httpx
from gdformat import enums
from gdformat import is_error
from gdformat import objects
from gdformat import responses
from gdformat.objects import Level
from gdformat.objects import Song

from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_SONG_ENDPOINT = "/getGJSongInfo.php"
_LEVEL_ENDPOINT = "/downloadGJLevel22.php"
_NOT_FOUND = "-1"
_NOT_ALLOWED = "-2"
_SONG_MAX_BYTES = 65_536
# Levels run to megabytes; the website proxy allows 30 s per request.
_LEVEL_TIMEOUT_SECONDS = 20.0
# The official firewall rejects any request carrying a user agent.
_USER_AGENT = ""


class BoomlingsError(StrEnum):
    NOT_FOUND = "not_found"
    NOT_ALLOWED = "not_allowed"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"
    TOO_LARGE = "too_large"


type BoomlingsResult[T] = T | BoomlingsError


async def _read_capped(response: httpx.Response, max_bytes: int) -> bytes | None:
    chunks: list[bytes] = []
    total = 0

    async for chunk in response.aiter_bytes():
        total += len(chunk)

        if total > max_bytes:
            return None

        chunks.append(chunk)

    return b"".join(chunks)


class BoomlingsClient:
    """A client for the official Geometry Dash servers."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        proxy_url: str | None,
    ) -> None:
        self._timeout_seconds = timeout_seconds

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

    async def _post(
        self,
        endpoint: str,
        data: dict[str, str],
        *,
        timeout_seconds: float,
        max_bytes: int,
    ) -> BoomlingsResult[str]:
        try:
            async with self._client.stream(
                "POST", endpoint, data=data, timeout=timeout_seconds
            ) as response:
                if response.status_code >= 500:
                    logger.warning(
                        "The official servers returned a server error.",
                        extra={
                            "endpoint": endpoint,
                            "status_code": response.status_code,
                        },
                    )

                    return BoomlingsError.UNAVAILABLE

                if response.status_code != 200:
                    return BoomlingsError.UNAVAILABLE

                body = await _read_capped(response, max_bytes)
        except httpx.HTTPError:
            logger.warning(
                "The official servers could not be reached.",
                extra={"endpoint": endpoint},
            )

            return BoomlingsError.UNAVAILABLE

        if body is None:
            logger.warning(
                "The official servers sent more than the caller allows.",
                extra={"endpoint": endpoint, "max_bytes": max_bytes},
            )

            return BoomlingsError.TOO_LARGE

        return body.decode("utf-8", "replace").strip()

    async def fetch_song(self, song_id: int) -> BoomlingsResult[Song]:
        content = await self._post(
            _SONG_ENDPOINT,
            {"songID": str(song_id), "secret": enums.Secret.COMMON},
            timeout_seconds=self._timeout_seconds,
            max_bytes=_SONG_MAX_BYTES,
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

    async def fetch_level(
        self, level_id: int, *, max_bytes: int
    ) -> BoomlingsResult[Level]:
        content = await self._post(
            _LEVEL_ENDPOINT,
            {"levelID": str(level_id), "secret": enums.Secret.COMMON},
            timeout_seconds=_LEVEL_TIMEOUT_SECONDS,
            max_bytes=max_bytes,
        )

        if isinstance(content, BoomlingsError):
            return content

        if content == _NOT_FOUND:
            return BoomlingsError.NOT_FOUND

        level = responses.parse_level_download(content)

        if is_error(level):
            logger.warning(
                "The official servers returned an unparseable level.",
                extra={"level_id": level_id, "error": level.value},
            )

            return BoomlingsError.MALFORMED

        return level


def default() -> BoomlingsClient:
    # Local import keeps this module importable without configuration.
    from poltergeist_core import settings

    return BoomlingsClient(
        base_url=settings.BOOMLINGS_URL,
        timeout_seconds=settings.BOOMLINGS_TIMEOUT_SECONDS,
        proxy_url=settings.BOOMLINGS_PROXY_URL or None,
    )

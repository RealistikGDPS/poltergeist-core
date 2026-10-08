from enum import StrEnum
from http import HTTPStatus

from poltergeist_core import settings
from poltergeist_core.resources import BanType
from poltergeist_core.resources import Permission
from poltergeist_core.resources import SongUploaded
from poltergeist_core.services import server_settings
from poltergeist_core.services import songs
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_DAILY_WINDOW = 86_400
# Served by the reverse proxy from object storage under this key.
_SONG_KEY = "songs/{song_id}.mp3"
_SONG_URL = "{public_url}/songs/{song_id}.mp3"


class SongUploadError(ServiceError, StrEnum):
    DISABLED = "disabled"
    NOT_PERMITTED = "not_permitted"
    BANNED = "banned"
    INVALID = "invalid"
    NOT_MP3 = "not_mp3"
    TOO_LARGE = "too_large"
    RATE_LIMITED = "rate_limited"

    def service(self) -> str:
        return "song_uploads"

    def status_code(self) -> int:
        match self:
            case (
                SongUploadError.DISABLED
                | SongUploadError.NOT_PERMITTED
                | SongUploadError.BANNED
            ):
                return HTTPStatus.FORBIDDEN
            case SongUploadError.INVALID | SongUploadError.NOT_MP3:
                return HTTPStatus.BAD_REQUEST
            case SongUploadError.TOO_LARGE:
                return HTTPStatus.CONTENT_TOO_LARGE
            case SongUploadError.RATE_LIMITED:
                return HTTPStatus.TOO_MANY_REQUESTS


def _is_mp3(data: bytes) -> bool:
    """An ID3 tag or a bare MPEG frame sync at the start of the file."""

    if data.startswith(b"ID3"):
        return True

    return len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0


async def upload_song(
    ctx: AbstractContext,
    *,
    actor_user_id: int,
    name: str,
    artist_name: str,
    data: bytes,
) -> SongUploadError.OnSuccess[int]:
    """Stores an MP3 as a custom song owned by the uploader. The daily
    allowance is charged last, so a refused file costs nothing."""

    site = await server_settings.current(ctx)

    if not site.song_upload_enabled:
        return SongUploadError.DISABLED

    if not await ctx.permissions.has(actor_user_id, Permission.SONGS_UPLOAD):
        return SongUploadError.NOT_PERMITTED

    if await ctx.bans.find_active(actor_user_id, BanType.SONG_UPLOAD) is not None:
        return SongUploadError.BANNED

    name = name.strip()
    artist_name = artist_name.strip()

    if not name or not artist_name:
        return SongUploadError.INVALID

    if len(name) > songs.NAME_LENGTH or len(artist_name) > songs.ARTIST_NAME_LENGTH:
        return SongUploadError.INVALID

    if len(data) > settings.APP_SONG_MAX_BYTES:
        return SongUploadError.TOO_LARGE

    if not _is_mp3(data):
        return SongUploadError.NOT_MP3

    within_allowance = await ctx.rate_limits.hit(
        "song_upload",
        str(actor_user_id),
        limit=site.song_upload_daily_limit,
        window_seconds=_DAILY_WINDOW,
    )

    if not within_allowance:
        return SongUploadError.RATE_LIMITED

    song_id = await ctx.songs.next_custom_id()

    await ctx.songs.create_custom(
        song_id,
        name=name,
        artist_id=await songs.artist_id_for(ctx, artist_name),
        size_bytes=len(data),
        url=_SONG_URL.format(public_url=settings.APP_PUBLIC_URL, song_id=song_id),
        uploaded_by_user_id=actor_user_id,
    )
    await ctx.storage.save(_SONG_KEY.format(song_id=song_id), data)

    user = await ctx.users.find_by_id(actor_user_id)

    if user is None:
        return SongUploadError.NOT_PERMITTED

    await ctx.events.publish(
        SongUploaded(
            song_id=song_id,
            song_name=name,
            artist_name=artist_name,
            user_id=actor_user_id,
            username=user.username,
            size_bytes=len(data),
        )
    )

    logger.info(
        "Song uploaded.",
        extra={"song_id": song_id, "user_id": actor_user_id, "size_bytes": len(data)},
    )

    return song_id

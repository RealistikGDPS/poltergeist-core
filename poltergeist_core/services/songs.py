from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus

from gdformat import codes
from gdformat import objects

from poltergeist_core import settings
from poltergeist_core.adapters.boomlings import BoomlingsError
from poltergeist_core.resources import CUSTOM_ID_END
from poltergeist_core.resources import CUSTOM_ID_START
from poltergeist_core.resources import Song
from poltergeist_core.resources import SongSource
from poltergeist_core.services import _wire
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_TOP_ARTISTS_PAGE_SIZE = 20
_BYTES_PER_MB = 1_048_576


class SongError(ServiceError, StrEnum):
    NOT_FOUND = "not_found"
    NOT_ALLOWED = "not_allowed"
    INVALID = "invalid"

    def service(self) -> str:
        return "songs"

    def status_code(self) -> int:
        match self:
            case SongError.NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case SongError.NOT_ALLOWED:
                return HTTPStatus.FORBIDDEN
            case SongError.INVALID:
                return HTTPStatus.BAD_REQUEST

    def code(self) -> int:
        match self:
            case SongError.NOT_ALLOWED:
                return codes.SongError.NOT_ALLOWED
            case _:
                return codes.SongError.NOT_FOUND


@dataclass(frozen=True, slots=True)
class TopArtistsPayload:
    artists: list[objects.TopArtist]
    page: objects.Page


def custom_content_url() -> str:
    return settings.APP_CUSTOM_CONTENT_URL


async def _fetch_upstream(ctx: AbstractContext, song_id: int) -> Song | None:
    # Ids in the custom range are ours; the official servers never know them.
    if CUSTOM_ID_START <= song_id < CUSTOM_ID_END:
        return None

    if await ctx.song_lookups.is_missing(song_id):
        return None

    # The official servers rate limit per egress address, which a proxy shares
    # with strangers; an outage or a 429 pauses every lookup for a minute.
    if await ctx.song_lookups.is_suspended():
        return None

    upstream = await ctx.boomlings.fetch_song(song_id)

    if isinstance(upstream, BoomlingsError):
        if upstream in (BoomlingsError.NOT_FOUND, BoomlingsError.NOT_ALLOWED):
            await ctx.song_lookups.mark_missing(song_id)

        if upstream is BoomlingsError.UNAVAILABLE:
            await ctx.song_lookups.suspend()

        return None

    source = SongSource.LIBRARY if song_id >= CUSTOM_ID_END else SongSource.NEWGROUNDS

    await ctx.artists.upsert_upstream(
        upstream.artist_id,
        upstream.artist_name,
        upstream.youtube_channel,
        scouted=upstream.scouted,
    )
    await ctx.songs.upsert_upstream(
        upstream.id,
        name=upstream.name,
        artist_id=upstream.artist_id,
        size_bytes=int(upstream.size_mb * _BYTES_PER_MB),
        url=upstream.url,
        source=source,
        video_id=upstream.video_id,
        priority=upstream.priority,
        nong=upstream.nong,
        is_new=upstream.is_new,
        new_badge=upstream.new_badge,
        soundtrack_url=upstream.soundtrack_url,
    )
    logger.info("Cached a song from the official servers.", extra={"song_id": song_id})

    return await ctx.songs.find_by_id(song_id)


async def ensure(ctx: AbstractContext, song_id: int) -> Song | None:
    """Returns the song, fetching it from the official servers when it is not
    known yet."""

    song = await ctx.songs.find_by_id(song_id)

    if song is not None:
        return song

    return await _fetch_upstream(ctx, song_id)


async def song_info(
    ctx: AbstractContext, song_id: int
) -> SongError.OnSuccess[objects.Song]:
    if song_id <= 0:
        return SongError.INVALID

    song = await ensure(ctx, song_id)

    if song is None:
        return SongError.NOT_FOUND

    if song.disabled_at is not None:
        return SongError.NOT_ALLOWED

    return _wire.song(song)


async def songs_for(ctx: AbstractContext, song_ids: list[int]) -> list[objects.Song]:
    unique = list(dict.fromkeys(song_id for song_id in song_ids if song_id > 0))
    songs = await ctx.songs.find_many_by_ids(unique)

    return [_wire.song(song) for song in songs if song.disabled_at is None]


async def top_artists(
    ctx: AbstractContext, page: int
) -> SongError.OnSuccess[TopArtistsPayload]:
    artists = await ctx.artists.list_top(page, _TOP_ARTISTS_PAGE_SIZE)
    total = await ctx.artists.count()

    return TopArtistsPayload(
        artists=[
            objects.TopArtist(name=artist.name, youtube_channel=artist.youtube_channel)
            for artist in artists
        ],
        page=objects.Page(total, page * _TOP_ARTISTS_PAGE_SIZE, _TOP_ARTISTS_PAGE_SIZE),
    )


async def artist_id_for(ctx: AbstractContext, artist_name: str) -> int:
    """Returns the artist with this name, creating a bare one when unknown."""

    artist = await ctx.artists.find_by_name(artist_name)

    if artist is not None:
        return artist.id

    return await ctx.artists.create(artist_name, "", "")


async def create_custom(
    ctx: AbstractContext,
    *,
    name: str,
    artist_name: str,
    url: str,
    size_bytes: int,
    uploaded_by_user_id: int | None,
) -> SongError.OnSuccess[Song]:
    if not name.strip() or not artist_name.strip() or not url.startswith("http"):
        return SongError.INVALID

    song_id = await ctx.songs.create_custom(
        name=name.strip(),
        artist_id=await artist_id_for(ctx, artist_name.strip()),
        size_bytes=size_bytes,
        url=url,
        uploaded_by_user_id=uploaded_by_user_id,
    )
    song = await ctx.songs.find_by_id(song_id)

    if song is None:
        return SongError.NOT_FOUND

    return song

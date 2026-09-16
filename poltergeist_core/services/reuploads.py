from enum import StrEnum
from http import HTTPStatus

from gdformat.enums import Rating
from gdformat.enums import Visibility
from gdformat.objects import Level

from poltergeist_core import settings
from poltergeist_core.adapters.boomlings import BoomlingsError
from poltergeist_core.resources import CUSTOM_ID_END
from poltergeist_core.resources import CUSTOM_ID_START
from poltergeist_core.resources import BanType
from poltergeist_core.resources import LevelUploaded
from poltergeist_core.resources import Permission
from poltergeist_core.resources import User
from poltergeist_core.services import levels
from poltergeist_core.services import moderation
from poltergeist_core.services import server_settings
from poltergeist_core.services import songs
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.services._common import is_error
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_ID_MAX = 2**31
_DAILY_WINDOW = 86_400
_UPSTREAM_LIMIT = 20
_UPSTREAM_WINDOW = 60
_UPSTREAM_KEY = "global"
# Room for the hashes, creator and song segments around the level itself.
_RESPONSE_SLACK = 16_384
_REQUESTED_STARS_MAX = 10


class ReuploadError(ServiceError, StrEnum):
    DISABLED = "disabled"
    BOT_UNAVAILABLE = "bot_unavailable"
    NOT_PERMITTED = "not_permitted"
    BANNED = "banned"
    INVALID_ID = "invalid_id"
    INVALID = "invalid"
    ALREADY_REUPLOADED = "already_reuploaded"
    IN_PROGRESS = "in_progress"
    RATE_LIMITED = "rate_limited"
    BUSY = "busy"
    UPSTREAM_NOT_FOUND = "upstream_not_found"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    UPSTREAM_MALFORMED = "upstream_malformed"
    TOO_LARGE = "too_large"

    def service(self) -> str:
        return "reuploads"

    def status_code(self) -> int:
        match self:
            case (
                ReuploadError.DISABLED
                | ReuploadError.NOT_PERMITTED
                | ReuploadError.BANNED
            ):
                return HTTPStatus.FORBIDDEN
            case ReuploadError.INVALID_ID | ReuploadError.INVALID:
                return HTTPStatus.BAD_REQUEST
            case ReuploadError.ALREADY_REUPLOADED | ReuploadError.IN_PROGRESS:
                return HTTPStatus.CONFLICT
            case ReuploadError.RATE_LIMITED:
                return HTTPStatus.TOO_MANY_REQUESTS
            case (
                ReuploadError.BOT_UNAVAILABLE
                | ReuploadError.BUSY
                | ReuploadError.UPSTREAM_UNAVAILABLE
            ):
                return HTTPStatus.SERVICE_UNAVAILABLE
            case ReuploadError.UPSTREAM_NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case ReuploadError.UPSTREAM_MALFORMED:
                return HTTPStatus.BAD_GATEWAY
            case ReuploadError.TOO_LARGE:
                return HTTPStatus.CONTENT_TOO_LARGE


def _custom_song_id(song_id: int) -> int | None:
    # Ids in the custom range would point at this server's own songs.
    if song_id == 0 or CUSTOM_ID_START <= song_id < CUSTOM_ID_END:
        return None

    return song_id


async def _fetch(
    ctx: AbstractContext, official_id: int
) -> ReuploadError.OnSuccess[Level]:
    fetched = await ctx.boomlings.fetch_level(
        official_id, max_bytes=settings.APP_LEVEL_MAX_BYTES + _RESPONSE_SLACK
    )

    match fetched:
        case BoomlingsError.UNAVAILABLE:
            await ctx.upstream.suspend()

            return ReuploadError.UPSTREAM_UNAVAILABLE
        case BoomlingsError.NOT_FOUND | BoomlingsError.NOT_ALLOWED:
            await ctx.upstream.mark_level_missing(official_id)

            return ReuploadError.UPSTREAM_NOT_FOUND
        case BoomlingsError.MALFORMED:
            return ReuploadError.UPSTREAM_MALFORMED
        case BoomlingsError.TOO_LARGE:
            return ReuploadError.TOO_LARGE
        case _:
            return fetched


async def _fetch_and_create(
    ctx: AbstractContext,
    bot: User,
    *,
    actor_user_id: int,
    official_id: int,
    daily_limit: int,
) -> ReuploadError.OnSuccess[int]:
    # Every attempt that would reach the official servers is charged, and the
    # player's own allowance before the shared one so a capped player cannot
    # drain it.
    within_allowance = await ctx.rate_limits.hit(
        "reupload",
        str(actor_user_id),
        limit=daily_limit,
        window_seconds=_DAILY_WINDOW,
    )

    if not within_allowance:
        return ReuploadError.RATE_LIMITED

    within_cap = await ctx.rate_limits.hit(
        "reupload_upstream",
        _UPSTREAM_KEY,
        limit=_UPSTREAM_LIMIT,
        window_seconds=_UPSTREAM_WINDOW,
    )

    if not within_cap:
        return ReuploadError.BUSY

    official = await _fetch(ctx, official_id)

    if is_error(official):
        return official

    name = levels.level_name(official.name)

    validation = await levels.validate_level_content(
        name=name,
        description=official.description,
        extra_string=official.extra_string,
        coins=official.coins,
        requested_stars=official.requested_stars,
        object_count=official.objects,
        version=official.version,
        level_string=official.level_string,
    )

    if validation is levels.LevelError.TOO_LARGE:
        return ReuploadError.TOO_LARGE

    if validation is not None:
        return ReuploadError.INVALID

    password = levels.copy_password(official.password)

    if is_error(password):
        return ReuploadError.INVALID

    copyable, copy_password = password
    custom_song_id = _custom_song_id(official.custom_song_id)

    if custom_song_id is not None:
        await songs.ensure(ctx, custom_song_id)

    original_id = None

    if official.original_id:
        original = await ctx.levels.find_by_official_id(official.original_id)
        original_id = None if original is None else original.id

    version = max(official.version, 1)

    level_id = await ctx.levels.create(
        user_id=bot.id,
        name=name,
        description=official.description,
        version=version,
        length=official.length,
        official_song_id=official.official_song,
        custom_song_id=custom_song_id,
        game_version=official.game_version,
        binary_version=0,
        visibility=Visibility.PUBLIC,
        two_player=official.two_player,
        low_detail_mode=official.low_detail_mode,
        original_id=original_id,
        official_id=official_id,
        copyable=copyable,
        copy_password=copy_password,
        object_count=official.objects,
        coins=official.coins,
        requested_stars=min(official.requested_stars, _REQUESTED_STARS_MAX),
        editor_seconds=official.editor_time,
        editor_seconds_copies=official.editor_time_copies,
        verification_frames=official.verification_time,
    )

    await levels.store_level_data(
        ctx,
        level_id,
        level_string=official.level_string,
        extra_string=official.extra_string,
        song_ids=official.song_ids,
        sfx_ids=official.sfx_ids,
        replay="",
    )

    await ctx.levels.rate(
        level_id,
        stars=official.stars,
        difficulty=official.difficulty,
        coins_verified=False,
        feature_order=0,
        rating=Rating.NONE,
        rated_by_user_id=None,
    )

    await moderation.refresh_creator_points(ctx, bot.id)

    await ctx.events.publish(
        LevelUploaded(
            level_id=level_id,
            level_name=name,
            user_id=bot.id,
            username=bot.username,
            version=version,
        )
    )

    logger.info(
        "Level reuploaded.",
        extra={
            "level_id": level_id,
            "official_id": official_id,
            "user_id": actor_user_id,
            "bot_user_id": bot.id,
        },
    )

    return level_id


async def reupload_level(
    ctx: AbstractContext, *, actor_user_id: int, official_id: int
) -> ReuploadError.OnSuccess[int]:
    """Copies a level from the official servers under the reupload bot. Every
    refusal that needs no upstream request comes before the one that does."""

    site = await server_settings.current(ctx)

    if not site.level_reupload_enabled:
        return ReuploadError.DISABLED

    if site.reupload_bot_user_id == 0:
        return ReuploadError.BOT_UNAVAILABLE

    bot = await ctx.users.find_by_id(site.reupload_bot_user_id)

    if bot is None:
        return ReuploadError.BOT_UNAVAILABLE

    if not await ctx.permissions.has(actor_user_id, Permission.LEVELS_REUPLOAD):
        return ReuploadError.NOT_PERMITTED

    if await ctx.bans.find_active(actor_user_id, BanType.UPLOAD) is not None:
        return ReuploadError.BANNED

    if not 0 < official_id < _ID_MAX:
        return ReuploadError.INVALID_ID

    if await ctx.levels.official_id_claimed(official_id):
        return ReuploadError.ALREADY_REUPLOADED

    if await ctx.upstream.is_level_missing(official_id):
        return ReuploadError.UPSTREAM_NOT_FOUND

    if await ctx.upstream.is_suspended():
        return ReuploadError.UPSTREAM_UNAVAILABLE

    if not await ctx.upstream.claim_level(official_id):
        return ReuploadError.IN_PROGRESS

    # NOTE: An uncaught failure skips the release; the claim then expires on
    # its own.
    result = await _fetch_and_create(
        ctx,
        bot,
        actor_user_id=actor_user_id,
        official_id=official_id,
        daily_limit=site.level_reupload_daily_limit,
    )

    await ctx.upstream.release_level(official_id)

    return result

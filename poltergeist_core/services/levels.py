import asyncio
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import replace
from datetime import timedelta
from enum import StrEnum
from http import HTTPStatus

from gdformat import encoding
from gdformat import objects
from gdformat import requests
from gdformat.enums import Difficulty
from gdformat.enums import Rating
from gdformat.enums import SearchDifficulty
from gdformat.enums import SearchType
from gdformat.enums import TimelyType
from gdformat.enums import Visibility
from gdformat.requests import DownloadLevelRequest
from gdformat.requests import LevelSearchRequest
from gdformat.requests import RateStarsRequest
from gdformat.requests import UploadLevelRequest

from poltergeist_core import settings
from poltergeist_core.resources import BanType
from poltergeist_core.resources import Level
from poltergeist_core.resources import LevelDeleted
from poltergeist_core.resources import LevelOrder
from poltergeist_core.resources import LevelSearch
from poltergeist_core.resources import LevelUpdated
from poltergeist_core.resources import LevelUploaded
from poltergeist_core.resources import ModTarget
from poltergeist_core.resources import Permission
from poltergeist_core.resources import User
from poltergeist_core.services import _audit
from poltergeist_core.services import _wire
from poltergeist_core.services import server_settings
from poltergeist_core.services import songs
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.services._common import is_error
from poltergeist_core.services.auth import Session
from poltergeist_core.utilities import clock
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_PAGE_SIZE = 10
_ID_LIST_MAX = 100
_NAME_MAX = 20
_DESCRIPTION_MAX = 300
_EXTRA_STRING_MAX = 1024
_COINS_MAX = 3
_REQUESTED_STARS_MAX = 10
_PASSWORD_DIGITS_MAX = 6
_UPLOAD_LIMIT = 10
_UPLOAD_WINDOW = 600
_TRENDING_WINDOW = timedelta(days=7)
_MAGIC_MIN_OBJECTS = 10_000
_DEMON_OFFSET = 5
_LEVEL_KEY = "levels/{level_id}.dat"
_REPLAY_KEY = "replays/{level_id}.dat"
_ID_SEPARATOR = ","
_MAX_STAR_VOTE = 10
_PUBLIC_PAGE_MAX = 50


class LevelError(ServiceError, StrEnum):
    NOT_FOUND = "not_found"
    NOT_PERMITTED = "not_permitted"
    INVALID = "invalid"
    TOO_LARGE = "too_large"
    BAD_SEED = "bad_seed"
    BAD_CHK = "bad_chk"
    LOCKED = "locked"
    BANNED = "banned"
    RATE_LIMITED = "rate_limited"
    UPLOADS_DISABLED = "uploads_disabled"

    def service(self) -> str:
        return "levels"

    def status_code(self) -> int:
        match self:
            case LevelError.NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case (
                LevelError.NOT_PERMITTED
                | LevelError.LOCKED
                | LevelError.BANNED
                | LevelError.UPLOADS_DISABLED
            ):
                return HTTPStatus.FORBIDDEN
            case LevelError.TOO_LARGE:
                return HTTPStatus.CONTENT_TOO_LARGE
            case LevelError.RATE_LIMITED:
                return HTTPStatus.TOO_MANY_REQUESTS
            case LevelError.INVALID | LevelError.BAD_SEED | LevelError.BAD_CHK:
                return HTTPStatus.BAD_REQUEST


@dataclass(frozen=True, slots=True)
class SearchPayload:
    levels: list[objects.LevelPreview]
    creators: list[objects.UserRef]
    songs: list[objects.Song]
    page: objects.Page


@dataclass(frozen=True, slots=True)
class LevelListing:
    """A public listing with its creators, for pages outside the game."""

    levels: list[Level]
    creators: dict[int, User]
    page: int
    size: int
    total: int


@dataclass(frozen=True, slots=True)
class DownloadPayload:
    level: objects.Level
    songs: list[objects.Song]
    creator: objects.UserRef | None


def _parse_ids(text: str) -> tuple[int, ...]:
    return tuple(
        int(part) for part in text.split(_ID_SEPARATOR) if part.strip().isdecimal()
    )[:_ID_LIST_MAX]


def _difficulties(
    request: LevelSearchRequest,
) -> tuple[Difficulty, ...] | None:
    if not request.difficulties:
        return None

    chosen: list[Difficulty] = []

    for entry in request.difficulties:
        match entry:
            case SearchDifficulty.NA:
                chosen.append(Difficulty.NA)
            case SearchDifficulty.AUTO:
                chosen.append(Difficulty.AUTO)
            case SearchDifficulty.DEMON:
                if request.demon_filter is None:
                    chosen.extend(d for d in Difficulty if d.is_demon)
                else:
                    chosen.append(Difficulty(_DEMON_OFFSET + request.demon_filter))
            case _:
                chosen.append(Difficulty(int(entry)))

    return tuple(chosen)


def _ratings(request: LevelSearchRequest) -> tuple[Rating, ...] | None:
    ratings = []

    if request.epic:
        ratings.append(Rating.EPIC)

    if request.legendary:
        ratings.append(Rating.LEGENDARY)

    if request.mythic:
        ratings.append(Rating.MYTHIC)

    return tuple(ratings) or None


def _apply_filters(search: LevelSearch, request: LevelSearchRequest) -> LevelSearch:
    official_song = None
    custom_song = None

    if request.song_id is not None:
        if request.custom_song:
            custom_song = request.song_id
        else:
            official_song = request.song_id

    exclude = request.completed_level_ids if request.uncompleted else None
    only = request.completed_level_ids if request.only_completed else None

    return replace(
        search,
        difficulties=_difficulties(request),
        lengths=tuple(request.lengths) or None,
        exclude_ids=exclude,
        only_ids=only,
        featured=request.featured,
        original=request.original,
        two_player=request.two_player,
        coins=request.coins,
        ratings=_ratings(request),
        rated=request.rated,
        unrated=request.unrated,
        official_song_id=official_song,
        custom_song_id=custom_song,
    )


async def _build_search(
    ctx: AbstractContext,
    session: Session,
    request: LevelSearchRequest,
) -> LevelError.OnSuccess[LevelSearch | None]:
    viewer = session.user.id
    friend_ids = tuple(await ctx.friendships.list_friend_ids(viewer))
    query = request.query.strip()

    base = LevelSearch(
        order=LevelOrder.LIKES,
        page=request.page,
        size=_PAGE_SIZE,
        viewer_user_id=viewer,
        friend_ids=friend_ids,
    )

    if request.gauntlet_id is not None:
        gauntlet = await ctx.gauntlets.find_by_id(request.gauntlet_id)

        if gauntlet is None:
            return None

        return replace(
            base,
            order=LevelOrder.GIVEN,
            level_ids=tuple(gauntlet.level_ids),
            include_unlisted=True,
            size=_ID_LIST_MAX,
        )

    match request.search_type:
        case SearchType.QUERY:
            if query.isdecimal():
                return replace(
                    base,
                    order=LevelOrder.GIVEN,
                    level_ids=(int(query),),
                    include_unlisted=True,
                )

            return _apply_filters(
                replace(base, name_prefix=query or None, order=LevelOrder.LIKES),
                request,
            )
        case SearchType.MOST_DOWNLOADED:
            return _apply_filters(replace(base, order=LevelOrder.DOWNLOADS), request)
        case SearchType.MOST_LIKED | SearchType.MOST_LIKED_WORLD:
            return _apply_filters(replace(base, order=LevelOrder.LIKES), request)
        case SearchType.TRENDING:
            return _apply_filters(
                replace(
                    base,
                    order=LevelOrder.LIKES,
                    uploaded_after=clock.now() - _TRENDING_WINDOW,
                ),
                request,
            )
        case SearchType.RECENT:
            return _apply_filters(replace(base, order=LevelOrder.UPLOADED), request)
        case SearchType.BY_USER:
            if not query.isdecimal():
                return None

            creator = int(query)

            return _apply_filters(
                replace(
                    base,
                    order=LevelOrder.UPLOADED,
                    creator_ids=(creator,),
                    include_all_visibilities=request.local and creator == viewer,
                ),
                request,
            )
        case SearchType.FEATURED | SearchType.FEATURED_WORLD:
            return _apply_filters(
                replace(
                    base,
                    order=LevelOrder.FEATURED,
                    featured=True,
                    player_creators_only=True,
                ),
                request,
            )
        case SearchType.MAGIC:
            return _apply_filters(
                replace(
                    base, order=LevelOrder.UPLOADED, min_objects=_MAGIC_MIN_OBJECTS
                ),
                request,
            )
        case SearchType.SENT_LEGACY | SearchType.SENT:
            if not await ctx.permissions.has(
                viewer, Permission.LEVELS_VIEW_SUGGESTIONS
            ):
                return LevelError.NOT_PERMITTED

            return replace(
                base,
                order=LevelOrder.SUGGESTED,
                pending_suggestions=True,
                include_unlisted=True,
            )
        case SearchType.LEVEL_IDS:
            ids = _parse_ids(query)

            return replace(
                base,
                order=LevelOrder.GIVEN,
                level_ids=ids,
                include_unlisted=True,
                size=max(len(ids), 1),
                page=0,
            )
        case SearchType.LEVEL_IDS_PAGED:
            return replace(
                base,
                order=LevelOrder.GIVEN,
                level_ids=_parse_ids(query),
                include_unlisted=True,
            )
        case SearchType.LOCAL_LEVEL_LIST:
            return replace(
                base,
                order=LevelOrder.GIVEN,
                level_ids=_parse_ids(query),
                include_unlisted=True,
                size=_ID_LIST_MAX,
                page=0,
            )
        case SearchType.AWARDED:
            return _apply_filters(
                replace(base, order=LevelOrder.RATED, rated=True), request
            )
        case SearchType.FOLLOWED:
            return _apply_filters(
                replace(
                    base,
                    order=LevelOrder.UPLOADED,
                    creator_ids=request.followed_account_ids,
                ),
                request,
            )
        case SearchType.FRIENDS:
            return _apply_filters(
                replace(base, order=LevelOrder.UPLOADED, creator_ids=friend_ids),
                request,
            )
        case SearchType.HALL_OF_FAME:
            return _apply_filters(
                replace(
                    base,
                    order=LevelOrder.FEATURED,
                    ratings=(Rating.EPIC, Rating.LEGENDARY, Rating.MYTHIC),
                    player_creators_only=True,
                ),
                request,
            )
        case SearchType.DAILY_HISTORY:
            return replace(base, order=LevelOrder.TIMELY, timely_type=TimelyType.DAILY)
        case SearchType.WEEKLY_HISTORY:
            return replace(base, order=LevelOrder.TIMELY, timely_type=TimelyType.WEEKLY)
        case SearchType.EVENT_HISTORY:
            return replace(base, order=LevelOrder.TIMELY, timely_type=TimelyType.EVENT)
        case SearchType.REPORTED:
            if not await ctx.permissions.has(viewer, Permission.LEVELS_VIEW_REPORTS):
                return LevelError.NOT_PERMITTED

            return replace(
                base,
                order=LevelOrder.REPORTED,
                open_reports=True,
                include_all_visibilities=True,
            )
        case SearchType.LEVEL_LIST:
            if not query.isdecimal():
                return None

            level_list = await ctx.level_lists.find_by_id(int(query))

            if level_list is None:
                return None

            if await ctx.download_marks.mark("list", level_list.id, viewer):
                await ctx.level_lists.increment_downloads(level_list.id)

            return replace(
                base,
                order=LevelOrder.GIVEN,
                level_ids=tuple(await ctx.level_lists.list_level_ids(level_list.id)),
                include_unlisted=True,
            )
        case SearchType.UNKNOWN_18 | SearchType.LITE_WEEKLY | SearchType.LITE_BONUS:
            return None


async def search(
    ctx: AbstractContext,
    session: Session,
    request: LevelSearchRequest,
) -> LevelError.OnSuccess[SearchPayload]:
    built = await _build_search(ctx, session, request)

    if is_error(built):
        return built

    page = max(request.page, 0)

    if built is None:
        return SearchPayload(
            levels=[],
            creators=[],
            songs=[],
            page=objects.Page(0, page * _PAGE_SIZE, _PAGE_SIZE),
        )

    levels = await ctx.levels.search(built)
    total = await ctx.levels.count(built)
    creators = await ctx.users.find_many_by_ids(
        list({level.user_id for level in levels})
    )

    song_objects = await songs.songs_for(
        ctx, [level.custom_song_id for level in levels if level.custom_song_id]
    )

    return SearchPayload(
        levels=[
            _wire.level_preview(level, gauntlet=request.gauntlet_id is not None)
            for level in levels
        ],
        creators=[_wire.user_ref(creator) for creator in creators],
        songs=song_objects,
        page=objects.Page(total, built.page * built.size, built.size),
    )


async def _visible(ctx: AbstractContext, session: Session, level: Level) -> bool:
    if level.visibility is not Visibility.FRIENDS or level.user_id == session.user.id:
        return True

    return await ctx.friendships.are_friends(session.user.id, level.user_id)


async def download(
    ctx: AbstractContext, session: Session, request: DownloadLevelRequest
) -> LevelError.OnSuccess[DownloadPayload]:
    timely_id = None
    timely_type = request.timely

    if timely_type is None:
        level_id = request.level_id
    else:
        current = await ctx.timely.find_current(timely_type)

        if current is None:
            return LevelError.NOT_FOUND

        level_id = current.level_id
        timely_id = current.wire_id

    level = await ctx.levels.find_by_id(level_id)

    if level is None or not await _visible(ctx, session, level):
        return LevelError.NOT_FOUND

    data = await ctx.level_data.find_by_level_id(level.id)
    stored = await ctx.storage.load(_LEVEL_KEY.format(level_id=level.id))

    if data is None or stored is None:
        logger.error(
            "Level row exists without stored data.", extra={"level_id": level.id}
        )

        return LevelError.NOT_FOUND

    if request.increment_downloads and await ctx.download_marks.mark(
        "level", level.id, session.user.id
    ):
        await ctx.levels.increment_downloads(level.id)
        level = replace_downloads(level)

    song_ids = list(data.song_ids)

    if level.custom_song_id:
        song_ids.insert(0, level.custom_song_id)

    song_objects = await songs.songs_for(ctx, song_ids)
    creator = None

    if timely_id is not None:
        creator_user = await ctx.users.find_by_id(level.user_id)
        creator = None if creator_user is None else _wire.user_ref(creator_user)

    return DownloadPayload(
        level=_wire.level_full(
            level,
            data,
            stored.decode(),
            timely_id=timely_id,
            song_size=data.size_bytes,
        ),
        songs=song_objects,
        creator=creator,
    )


def replace_downloads(level: Level) -> Level:
    return level.model_copy(update={"downloads": level.downloads + 1})


def level_name(raw: str) -> str:
    return encoding.strip_separators(raw).strip()


def copy_password(
    password: str | None,
) -> LevelError.OnSuccess[tuple[bool, int | None]]:
    if password is None:
        return False, None

    if password == "":
        return True, None

    if not password.isdecimal() or len(password) > _PASSWORD_DIGITS_MAX:
        return LevelError.INVALID

    return True, int(password)


async def validate_level_content(
    *,
    name: str,
    description: str,
    extra_string: str,
    coins: int,
    requested_stars: int,
    object_count: int,
    version: int,
    level_string: str,
) -> LevelError.OnSuccess[None]:
    if not name or len(name) > _NAME_MAX:
        return LevelError.INVALID

    if len(description) > _DESCRIPTION_MAX:
        return LevelError.INVALID

    if len(extra_string) > _EXTRA_STRING_MAX:
        return LevelError.INVALID

    if not 0 <= coins <= _COINS_MAX:
        return LevelError.INVALID

    if requested_stars < 0 or object_count < 0 or version < 0:
        return LevelError.INVALID

    if len(level_string) > settings.APP_LEVEL_MAX_BYTES:
        return LevelError.TOO_LARGE

    # Decompressing a multi-megabyte level would stall the event loop.
    decompressed = await asyncio.to_thread(encoding.decompress_level, level_string)

    if decompressed is None:
        return LevelError.INVALID

    return None


async def store_level_data(
    ctx: AbstractContext,
    level_id: int,
    *,
    level_string: str,
    extra_string: str,
    song_ids: Sequence[int],
    sfx_ids: Sequence[int],
    replay: str,
) -> None:
    level_bytes = level_string.encode()
    await ctx.storage.save(_LEVEL_KEY.format(level_id=level_id), level_bytes)

    if replay:
        await ctx.storage.save(_REPLAY_KEY.format(level_id=level_id), replay.encode())

    await ctx.level_data.upsert(
        level_id,
        size_bytes=len(level_bytes),
        sha1=hashlib.sha1(level_bytes).digest(),
        extra_string=extra_string,
        song_ids=json.dumps(list(song_ids)),
        sfx_ids=json.dumps(list(sfx_ids)),
        has_replay=bool(replay),
    )


async def upload(
    ctx: AbstractContext, session: Session, request: UploadLevelRequest
) -> LevelError.OnSuccess[int]:
    if not requests.verify_level_seed(request):
        return LevelError.BAD_SEED

    user_id = session.user.id

    if not await ctx.permissions.has(user_id, Permission.LEVELS_UPLOAD):
        return LevelError.NOT_PERMITTED

    if not (await server_settings.current(ctx)).level_uploads_enabled:
        return LevelError.UPLOADS_DISABLED

    if await ctx.bans.find_active(user_id, BanType.UPLOAD) is not None:
        return LevelError.BANNED

    name = level_name(request.name)

    validation = await validate_level_content(
        name=name,
        description=request.description,
        extra_string=request.extra_string,
        coins=request.coins,
        requested_stars=request.requested_stars,
        object_count=request.objects,
        version=request.version,
        level_string=request.level_string,
    )

    if validation is not None:
        return validation

    password = copy_password(request.password)

    if is_error(password):
        return password

    within_limit = await ctx.rate_limits.hit(
        "upload", str(user_id), limit=_UPLOAD_LIMIT, window_seconds=_UPLOAD_WINDOW
    )

    if not within_limit:
        return LevelError.RATE_LIMITED

    copyable, password_number = password
    custom_song_id = request.custom_song_id or None

    if custom_song_id is not None:
        await songs.ensure(ctx, custom_song_id)

    if request.level_id > 0:
        existing = await ctx.levels.find_by_id(request.level_id)

        if existing is None or existing.user_id != user_id:
            return LevelError.NOT_FOUND
    else:
        existing = await ctx.levels.find_by_user_and_name(user_id, name)

    original_id = request.original_id or None

    if original_id is not None and await ctx.levels.find_by_id(original_id) is None:
        original_id = None

    if existing is None:
        version = max(request.version, 1)

        level_id = await ctx.levels.create(
            user_id=user_id,
            name=name,
            description=request.description,
            version=version,
            length=request.length,
            official_song_id=request.official_song,
            custom_song_id=custom_song_id,
            game_version=session.client.game_version,
            binary_version=session.client.binary_version,
            visibility=request.visibility,
            two_player=request.two_player,
            low_detail_mode=request.low_detail_mode,
            original_id=original_id,
            official_id=None,
            copyable=copyable,
            copy_password=password_number,
            object_count=request.objects,
            coins=request.coins,
            requested_stars=min(request.requested_stars, _REQUESTED_STARS_MAX),
            editor_seconds=request.editor_time,
            editor_seconds_copies=request.editor_time_copies,
            verification_frames=request.verification_time,
        )
    else:
        if existing.update_locked:
            return LevelError.LOCKED

        level_id = existing.id
        version = max(request.version, existing.version)

        await ctx.levels.update(
            level_id,
            description=request.description,
            version=version,
            length=request.length,
            official_song_id=request.official_song,
            custom_song_id=custom_song_id,
            game_version=session.client.game_version,
            binary_version=session.client.binary_version,
            visibility=request.visibility,
            two_player=request.two_player,
            low_detail_mode=request.low_detail_mode,
            copyable=copyable,
            copy_password=password_number,
            object_count=request.objects,
            coins=request.coins,
            requested_stars=min(request.requested_stars, _REQUESTED_STARS_MAX),
            editor_seconds=request.editor_time,
            editor_seconds_copies=request.editor_time_copies,
            verification_frames=request.verification_time,
        )

    await store_level_data(
        ctx,
        level_id,
        level_string=request.level_string,
        extra_string=request.extra_string,
        song_ids=request.song_ids,
        sfx_ids=request.sfx_ids,
        replay=request.replay,
    )

    event_type = LevelUploaded if existing is None else LevelUpdated

    await ctx.events.publish(
        event_type(
            level_id=level_id,
            level_name=name,
            user_id=user_id,
            username=session.user.username,
            version=version,
        )
    )

    logger.info(
        "Level uploaded.",
        extra={
            "level_id": level_id,
            "user_id": user_id,
            "updated": existing is not None,
        },
    )

    return level_id


async def delete(
    ctx: AbstractContext, session: Session, level_id: int
) -> LevelError.OnSuccess[None]:
    level = await ctx.levels.find_by_id(level_id)

    if level is None:
        return LevelError.NOT_FOUND

    user_id = session.user.id
    is_owner = level.user_id == user_id

    if not is_owner and not await ctx.permissions.has(
        user_id, Permission.LEVELS_DELETE_ANY
    ):
        return LevelError.NOT_PERMITTED

    await ctx.levels.soft_delete(level.id)

    if not is_owner:
        await _audit.record(ctx, user_id, "delete", ModTarget.LEVEL, level.id)

    await ctx.events.publish(
        LevelDeleted(
            level_id=level.id,
            level_name=level.name,
            user_id=level.user_id,
            actor_user_id=user_id,
        )
    )

    logger.info("Level deleted.", extra={"level_id": level.id, "user_id": user_id})

    return None


async def update_description(
    ctx: AbstractContext, session: Session, level_id: int, description: str
) -> LevelError.OnSuccess[None]:
    level = await ctx.levels.find_by_id(level_id)

    if level is None:
        return LevelError.NOT_FOUND

    if len(description) > _DESCRIPTION_MAX:
        return LevelError.INVALID

    user_id = session.user.id

    if level.user_id != user_id and not await ctx.permissions.has(
        user_id, Permission.LEVELS_EDIT_ANY
    ):
        return LevelError.NOT_PERMITTED

    await ctx.levels.update_description(level.id, description)

    return None


async def report(
    ctx: AbstractContext, level_id: int, *, user_id: int | None, ip: bytes | None
) -> LevelError.OnSuccess[None]:
    level = await ctx.levels.find_by_id(level_id)

    if level is None:
        return LevelError.NOT_FOUND

    if user_id is not None and await ctx.reports.exists_open(level.id, user_id):
        return None

    if ip is not None and await ctx.reports.count_recent_by_ip(ip) >= _UPLOAD_LIMIT:
        return LevelError.RATE_LIMITED

    await ctx.reports.create(level.id, user_id, ip)

    return None


async def rate_stars(
    ctx: AbstractContext, session: Session, request: RateStarsRequest
) -> LevelError.OnSuccess[None]:
    """A player's difficulty vote. It is recorded for moderators but never
    changes the level by itself."""

    if not requests.verify_rate_chk(request):
        return LevelError.BAD_CHK

    if not 1 <= request.stars <= _MAX_STAR_VOTE:
        return LevelError.INVALID

    level = await ctx.levels.find_by_id(request.level_id)

    if level is None:
        return LevelError.NOT_FOUND

    await ctx.star_votes.upsert(level.id, session.user.id, request.stars)

    return None


async def _public_listing(ctx: AbstractContext, search: LevelSearch) -> LevelListing:
    levels = await ctx.levels.search(search)
    total = await ctx.levels.count(search)
    creators = await ctx.users.find_many_by_ids(
        list({level.user_id for level in levels})
    )

    return LevelListing(
        levels=levels,
        creators={creator.id: creator for creator in creators},
        page=search.page,
        size=search.size,
        total=total,
    )


def _public_search(order: LevelOrder, page: int, size: int) -> LevelSearch:
    """Without a viewer only public levels match, which is what an anonymous
    reader may see."""

    return LevelSearch(
        order=order,
        page=max(page, 0),
        size=min(max(size, 1), _PUBLIC_PAGE_MAX),
        player_creators_only=True,
    )


async def recent(ctx: AbstractContext, *, page: int, size: int) -> LevelListing:
    return await _public_listing(ctx, _public_search(LevelOrder.UPLOADED, page, size))


async def featured(ctx: AbstractContext, *, page: int, size: int) -> LevelListing:
    return await _public_listing(
        ctx, replace(_public_search(LevelOrder.FEATURED, page, size), featured=True)
    )

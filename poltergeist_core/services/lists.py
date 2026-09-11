from dataclasses import dataclass
from dataclasses import replace
from datetime import timedelta
from enum import StrEnum
from http import HTTPStatus

from gdformat import codes
from gdformat import encoding
from gdformat import objects
from gdformat import requests
from gdformat.enums import Difficulty
from gdformat.enums import ListSearchType
from gdformat.enums import SearchDifficulty
from gdformat.requests import ListSearchRequest
from gdformat.requests import UploadListRequest

from poltergeist_core.resources import BanType
from poltergeist_core.resources import ListOrder
from poltergeist_core.resources import ListSearch
from poltergeist_core.resources import ModTarget
from poltergeist_core.resources import Permission
from poltergeist_core.services import _wire
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.services.auth import Session
from poltergeist_core.utilities import clock
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_PAGE_SIZE = 10
_NAME_MAX = 64
_DESCRIPTION_MAX = 300
_LEVELS_MAX = 100
_UPLOAD_LIMIT = 10
_UPLOAD_WINDOW = 600
_TRENDING_WINDOW = timedelta(days=7)
_DEMON_OFFSET = 5


class ListError(ServiceError, StrEnum):
    NOT_FOUND = "not_found"
    NOT_PERMITTED = "not_permitted"
    INVALID = "invalid"
    BAD_SEED = "bad_seed"
    BANNED = "banned"
    RATE_LIMITED = "rate_limited"

    def service(self) -> str:
        return "lists"

    def status_code(self) -> int:
        match self:
            case ListError.NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case ListError.NOT_PERMITTED | ListError.BANNED:
                return HTTPStatus.FORBIDDEN
            case ListError.RATE_LIMITED:
                return HTTPStatus.TOO_MANY_REQUESTS
            case ListError.INVALID | ListError.BAD_SEED:
                return HTTPStatus.BAD_REQUEST

    def code(self) -> int:
        match self:
            case ListError.BAD_SEED:
                return codes.ListUploadError.BAD_SEED
            case _:
                return codes.ListUploadError.REJECTED


@dataclass(frozen=True, slots=True)
class ListSearchPayload:
    lists: list[objects.LevelList]
    creators: list[objects.UserRef]
    page: objects.Page


def _difficulties(request: ListSearchRequest) -> tuple[Difficulty, ...] | None:
    match request.difficulty:
        case None:
            return None
        case SearchDifficulty.NA:
            return (Difficulty.NA,)
        case SearchDifficulty.AUTO:
            return (Difficulty.AUTO,)
        case SearchDifficulty.DEMON:
            if request.demon_filter is None:
                return tuple(d for d in Difficulty if d.is_demon)

            return (Difficulty(_DEMON_OFFSET + request.demon_filter),)
        case _:
            return (Difficulty(int(request.difficulty)),)


async def _build_search(
    ctx: AbstractContext, session: Session, request: ListSearchRequest
) -> ListSearch | None:
    viewer = session.user.id
    friend_ids = tuple(await ctx.friendships.list_friend_ids(viewer))
    query = request.query.strip()

    base = ListSearch(
        order=ListOrder.LIKES,
        page=request.page,
        size=_PAGE_SIZE,
        viewer_user_id=viewer,
        friend_ids=friend_ids,
        difficulties=_difficulties(request),
        rated=request.rated,
    )

    match request.search_type:
        case ListSearchType.QUERY:
            if query.isdecimal():
                return replace(base, list_ids=(int(query),), include_unlisted=True)

            return replace(base, name_prefix=query or None)
        case ListSearchType.MOST_DOWNLOADED:
            return replace(base, order=ListOrder.DOWNLOADS)
        case ListSearchType.MOST_LIKED | ListSearchType.MAGIC | ListSearchType.TOP:
            return base
        case ListSearchType.TRENDING:
            return replace(base, uploaded_after=clock.now() - _TRENDING_WINDOW)
        case ListSearchType.RECENT:
            return replace(base, order=ListOrder.UPLOADED)
        case ListSearchType.BY_ACCOUNT:
            if not query.isdecimal():
                return None

            creator = int(query)

            return replace(
                base,
                order=ListOrder.UPLOADED,
                creator_ids=(creator,),
                include_all_visibilities=creator == viewer,
            )
        case ListSearchType.AWARDED:
            return replace(base, order=ListOrder.RATED, rated=True)
        case ListSearchType.FOLLOWED:
            return replace(
                base, order=ListOrder.UPLOADED, creator_ids=request.followed_account_ids
            )
        case ListSearchType.FRIENDS:
            return replace(base, order=ListOrder.UPLOADED, creator_ids=friend_ids)
        case ListSearchType.SENT:
            return None


async def search(
    ctx: AbstractContext, session: Session, request: ListSearchRequest
) -> ListError.OnSuccess[ListSearchPayload]:
    built = await _build_search(ctx, session, request)
    page = max(request.page, 0)

    if built is None:
        return ListSearchPayload(
            lists=[], creators=[], page=objects.Page(0, page * _PAGE_SIZE, _PAGE_SIZE)
        )

    lists = await ctx.level_lists.search(built)
    total = await ctx.level_lists.count(built)
    level_ids = await ctx.level_lists.list_level_ids_many([entry.id for entry in lists])
    creators = await ctx.users.find_many_by_ids(
        list({entry.user_id for entry in lists})
    )
    by_id = {creator.id: creator for creator in creators}

    return ListSearchPayload(
        lists=[
            _wire.level_list(entry, level_ids[entry.id], by_id[entry.user_id])
            for entry in lists
            if entry.user_id in by_id
        ],
        creators=[_wire.user_ref(creator) for creator in creators],
        page=objects.Page(total, page * _PAGE_SIZE, _PAGE_SIZE),
    )


async def upload(
    ctx: AbstractContext, session: Session, request: UploadListRequest
) -> ListError.OnSuccess[int]:
    if not requests.verify_list_seed(request):
        return ListError.BAD_SEED

    user_id = session.user.id

    if not await ctx.permissions.has(user_id, Permission.LISTS_UPLOAD):
        return ListError.NOT_PERMITTED

    if await ctx.bans.find_active(user_id, BanType.UPLOAD) is not None:
        return ListError.BANNED

    name = encoding.strip_separators(request.name).strip()

    if not name or len(name) > _NAME_MAX or len(request.description) > _DESCRIPTION_MAX:
        return ListError.INVALID

    if not request.level_ids or len(request.level_ids) > _LEVELS_MAX:
        return ListError.INVALID

    within_limit = await ctx.rate_limits.hit(
        "upload", str(user_id), limit=_UPLOAD_LIMIT, window_seconds=_UPLOAD_WINDOW
    )

    if not within_limit:
        return ListError.RATE_LIMITED

    known = {
        level.id for level in await ctx.levels.find_many_by_ids(list(request.level_ids))
    }
    level_ids = [
        level_id for level_id in dict.fromkeys(request.level_ids) if level_id in known
    ]

    if not level_ids:
        return ListError.INVALID

    if request.list_id > 0:
        existing = await ctx.level_lists.find_by_id(request.list_id)

        if existing is None or existing.user_id != user_id:
            return ListError.NOT_FOUND
    else:
        existing = await ctx.level_lists.find_by_user_and_name(user_id, name)

    if existing is None:
        original_id = request.original_id or None

        if (
            original_id is not None
            and await ctx.level_lists.find_by_id(original_id) is None
        ):
            original_id = None

        list_id = await ctx.level_lists.create(
            user_id=user_id,
            name=name,
            description=request.description,
            version=max(request.version, 1),
            difficulty=request.difficulty,
            visibility=request.visibility,
            original_id=original_id,
        )
    else:
        list_id = existing.id

        await ctx.level_lists.update(
            list_id,
            name=name,
            description=request.description,
            version=max(request.version, existing.version),
            difficulty=request.difficulty,
            visibility=request.visibility,
        )

    await ctx.level_lists.replace_levels(list_id, level_ids)
    logger.info("List uploaded.", extra={"list_id": list_id, "user_id": user_id})

    return list_id


async def delete(
    ctx: AbstractContext, session: Session, list_id: int
) -> ListError.OnSuccess[None]:
    level_list = await ctx.level_lists.find_by_id(list_id)

    if level_list is None:
        return ListError.NOT_FOUND

    user_id = session.user.id
    is_owner = level_list.user_id == user_id

    if not is_owner and not await ctx.permissions.has(
        user_id, Permission.LISTS_DELETE_ANY
    ):
        return ListError.NOT_PERMITTED

    await ctx.level_lists.soft_delete(level_list.id)

    if not is_owner:
        await ctx.mod_actions.create(
            user_id, "delete", ModTarget.LEVEL_LIST, level_list.id
        )

    return None

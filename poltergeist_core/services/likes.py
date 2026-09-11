from enum import StrEnum
from http import HTTPStatus

from gdformat import requests
from gdformat.enums import LikeType
from gdformat.requests import LikeRequest

from poltergeist_core.resources import LikeTarget
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.services.auth import Session

_LIKE_LIMIT = 60
_LIKE_WINDOW = 60


class LikeError(ServiceError, StrEnum):
    NOT_FOUND = "not_found"
    BAD_CHK = "bad_chk"
    ALREADY_VOTED = "already_voted"
    RATE_LIMITED = "rate_limited"

    def service(self) -> str:
        return "likes"

    def status_code(self) -> int:
        match self:
            case LikeError.NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case LikeError.BAD_CHK:
                return HTTPStatus.BAD_REQUEST
            case LikeError.ALREADY_VOTED:
                return HTTPStatus.CONFLICT
            case LikeError.RATE_LIMITED:
                return HTTPStatus.TOO_MANY_REQUESTS


def _target(item_type: LikeType) -> LikeTarget:
    match item_type:
        case LikeType.LEVEL:
            return LikeTarget.LEVEL
        case LikeType.LEVEL_COMMENT:
            return LikeTarget.COMMENT
        case LikeType.ACCOUNT_COMMENT:
            return LikeTarget.ACCOUNT_COMMENT
        case LikeType.LIST:
            return LikeTarget.LEVEL_LIST


async def _exists(ctx: AbstractContext, target: LikeTarget, item_id: int) -> bool:
    match target:
        case LikeTarget.LEVEL:
            return await ctx.levels.find_by_id(item_id) is not None
        case LikeTarget.COMMENT:
            return await ctx.comments.find_by_id(item_id) is not None
        case LikeTarget.ACCOUNT_COMMENT:
            return await ctx.account_comments.find_by_id(item_id) is not None
        case LikeTarget.LEVEL_LIST:
            return await ctx.level_lists.find_by_id(item_id) is not None


async def _apply(
    ctx: AbstractContext, target: LikeTarget, item_id: int, delta: int
) -> None:
    match target:
        case LikeTarget.LEVEL:
            await ctx.levels.add_likes(item_id, delta)
        case LikeTarget.COMMENT:
            await ctx.comments.add_likes(item_id, delta)
        case LikeTarget.ACCOUNT_COMMENT:
            await ctx.account_comments.add_likes(item_id, delta)
        case LikeTarget.LEVEL_LIST:
            await ctx.level_lists.add_likes(item_id, delta)


async def like(
    ctx: AbstractContext, session: Session, request: LikeRequest
) -> LikeError.OnSuccess[None]:
    if not requests.verify_like_chk(request):
        return LikeError.BAD_CHK

    within_limit = await ctx.rate_limits.hit(
        "like", str(session.user.id), limit=_LIKE_LIMIT, window_seconds=_LIKE_WINDOW
    )

    if not within_limit:
        return LikeError.RATE_LIMITED

    target = _target(request.item_type)

    if not await _exists(ctx, target, request.item_id):
        return LikeError.NOT_FOUND

    if await ctx.likes.find(session.user.id, target, request.item_id) is not None:
        return LikeError.ALREADY_VOTED

    await ctx.likes.create(
        session.user.id, target, request.item_id, is_like=request.like
    )
    await _apply(ctx, target, request.item_id, 1 if request.like else -1)

    return None

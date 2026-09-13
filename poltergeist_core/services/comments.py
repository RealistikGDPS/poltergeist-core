from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus

from gdformat import objects
from gdformat import requests
from gdformat.enums import CommentHistoryState
from gdformat.enums import CommentMode
from gdformat.requests import UploadAccountCommentRequest
from gdformat.requests import UploadCommentRequest

from poltergeist_core import settings
from poltergeist_core.resources import BanType
from poltergeist_core.resources import Comment
from poltergeist_core.resources import ModTarget
from poltergeist_core.resources import Permission
from poltergeist_core.services import _audit
from poltergeist_core.services import _badges
from poltergeist_core.services import _wire
from poltergeist_core.services import commands
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.services.auth import Session
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_LEVEL_COMMENT_MAX = 100
_ACCOUNT_COMMENT_MAX = 140
_PAGE_MAX = 100
_COMMENT_LIMIT = 15
_COMMENT_WINDOW = 60
_MAX_PERCENT = 100


class CommentError(ServiceError, StrEnum):
    NOT_FOUND = "not_found"
    NOT_PERMITTED = "not_permitted"
    BAD_CHK = "bad_chk"
    INVALID = "invalid"
    RATE_LIMITED = "rate_limited"

    def service(self) -> str:
        return "comments"

    def status_code(self) -> int:
        match self:
            case CommentError.NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case CommentError.NOT_PERMITTED:
                return HTTPStatus.FORBIDDEN
            case CommentError.RATE_LIMITED:
                return HTTPStatus.TOO_MANY_REQUESTS
            case CommentError.BAD_CHK | CommentError.INVALID:
                return HTTPStatus.BAD_REQUEST


@dataclass(frozen=True, slots=True)
class CommentBanned:
    seconds_left: int | None
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadOutcome:
    """Exactly one of the fields is set: the comment was stored, the player
    is banned from commenting, or the text was a command and this is its reply."""

    comment_id: int | None = None
    ban: CommentBanned | None = None
    reply: str | None = None


@dataclass(frozen=True, slots=True)
class CommentsPayload:
    comments: list[objects.Comment]
    page: objects.Page


@dataclass(frozen=True, slots=True)
class AccountCommentsPayload:
    comments: list[objects.AccountComment]
    page: objects.Page


def _page(total: int, page: int, size: int) -> objects.Page:
    return objects.Page(total, page * size, size)


def _page_size(count: int) -> int:
    return min(max(count, 1), _PAGE_MAX)


async def _ban(ctx: AbstractContext, user_id: int) -> CommentBanned | None:
    ban = await ctx.bans.find_active(user_id, BanType.COMMENT)

    if ban is None:
        return None

    return CommentBanned(seconds_left=ban.seconds_left, reason=ban.reason)


async def _hydrate(
    ctx: AbstractContext, entries: list[Comment], *, include_level_id: bool
) -> list[objects.Comment]:
    author_ids = list({entry.user_id for entry in entries})
    users = {user.id: user for user in await ctx.users.find_many_by_ids(author_ids)}
    stats = {
        entry.user_id: entry
        for entry in await ctx.stats.find_many_by_user_ids(author_ids)
    }
    granted = await ctx.permissions.effective_many(author_ids)
    comments = []

    for entry in entries:
        if entry.user_id not in users or entry.user_id not in stats:
            continue

        comments.append(
            _wire.comment(
                entry,
                users[entry.user_id],
                stats[entry.user_id],
                _badges.mod_level(granted[entry.user_id]),
                include_level_id=include_level_id,
            )
        )

    return comments


async def upload_level_comment(
    ctx: AbstractContext, session: Session, request: UploadCommentRequest
) -> CommentError.OnSuccess[UploadOutcome]:
    if not requests.verify_comment_chk(request):
        return CommentError.BAD_CHK

    user_id = session.user.id
    content = request.content.strip()

    if not content or len(content) > _LEVEL_COMMENT_MAX:
        return CommentError.INVALID

    if not 0 <= request.percent <= _MAX_PERCENT:
        return CommentError.INVALID

    banned = await _ban(ctx, user_id)

    if banned is not None:
        return UploadOutcome(ban=banned)

    if content.startswith(settings.APP_COMMAND_PREFIX):
        if not await ctx.permissions.has(user_id, Permission.COMMANDS_USE):
            return CommentError.NOT_PERMITTED

        reply = await commands.execute(ctx, session, content, request.level_id)

        return UploadOutcome(reply=reply)

    if not await ctx.permissions.has(user_id, Permission.COMMENTS_POST):
        return CommentError.NOT_PERMITTED

    within_limit = await ctx.rate_limits.hit(
        "comment", str(user_id), limit=_COMMENT_LIMIT, window_seconds=_COMMENT_WINDOW
    )

    if not within_limit:
        return CommentError.RATE_LIMITED

    if request.level_id < 0:
        level_list = await ctx.level_lists.find_by_id(-request.level_id)

        if level_list is None:
            return CommentError.NOT_FOUND

        comment_id = await ctx.comments.create(
            user_id=user_id,
            level_id=None,
            list_id=level_list.id,
            content=content,
            percent=0,
        )
    else:
        level = await ctx.levels.find_by_id(request.level_id)

        if level is None:
            return CommentError.NOT_FOUND

        comment_id = await ctx.comments.create(
            user_id=user_id,
            level_id=level.id,
            list_id=None,
            content=content,
            percent=request.percent,
        )

    return UploadOutcome(comment_id=comment_id)


async def delete_level_comment(
    ctx: AbstractContext, session: Session, comment_id: int
) -> CommentError.OnSuccess[None]:
    comment = await ctx.comments.find_by_id(comment_id)

    if comment is None:
        return CommentError.NOT_FOUND

    user_id = session.user.id
    allowed = comment.user_id == user_id

    if not allowed and comment.level_id is not None:
        level = await ctx.levels.find_by_id(comment.level_id)
        allowed = level is not None and level.user_id == user_id

    if not allowed and comment.list_id is not None:
        level_list = await ctx.level_lists.find_by_id(comment.list_id)
        allowed = level_list is not None and level_list.user_id == user_id

    if not allowed and not await ctx.permissions.has(
        user_id, Permission.COMMENTS_DELETE_ANY
    ):
        return CommentError.NOT_PERMITTED

    await ctx.comments.soft_delete(comment.id)

    if not allowed:
        await _audit.record(ctx, user_id, "delete", ModTarget.COMMENT, comment.id)

    return None


async def list_level_comments(
    ctx: AbstractContext, level_id: int, mode: CommentMode, page: int, count: int
) -> CommentError.OnSuccess[CommentsPayload]:
    size = _page_size(count)

    if level_id < 0:
        entries = await ctx.comments.list_by_list(-level_id, mode, page, size)
        total = await ctx.comments.count_by_list(-level_id)
    else:
        entries = await ctx.comments.list_by_level(level_id, mode, page, size)
        total = await ctx.comments.count_by_level(level_id)

    return CommentsPayload(
        comments=await _hydrate(ctx, entries, include_level_id=False),
        page=_page(total, page, size),
    )


async def comment_history(
    ctx: AbstractContext,
    session: Session,
    target_user_id: int,
    mode: CommentMode,
    page: int,
    count: int,
) -> CommentError.OnSuccess[CommentsPayload]:
    target = await ctx.users.find_by_id(target_user_id)

    if target is None:
        return CommentError.NOT_FOUND

    size = _page_size(count)
    viewer_id = session.user.id
    visible = target.id == viewer_id

    if not visible:
        match target.comment_history_privacy:
            case CommentHistoryState.ALL:
                visible = True
            case CommentHistoryState.FRIENDS:
                visible = await ctx.friendships.are_friends(viewer_id, target.id)
            case CommentHistoryState.NONE:
                visible = False

    if not visible:
        visible = await ctx.permissions.has(viewer_id, Permission.COMMENTS_DELETE_ANY)

    if not visible:
        return CommentsPayload(comments=[], page=_page(0, page, size))

    entries = await ctx.comments.list_by_user(target.id, mode, page, size)
    total = await ctx.comments.count_by_user(target.id)

    return CommentsPayload(
        comments=await _hydrate(ctx, entries, include_level_id=True),
        page=_page(total, page, size),
    )


async def upload_account_comment(
    ctx: AbstractContext, session: Session, request: UploadAccountCommentRequest
) -> CommentError.OnSuccess[UploadOutcome]:
    user_id = session.user.id
    content = request.content.strip()

    if not content or len(content) > _ACCOUNT_COMMENT_MAX:
        return CommentError.INVALID

    banned = await _ban(ctx, user_id)

    if banned is not None:
        return UploadOutcome(ban=banned)

    if not await ctx.permissions.has(user_id, Permission.PROFILE_POST):
        return CommentError.NOT_PERMITTED

    within_limit = await ctx.rate_limits.hit(
        "comment", str(user_id), limit=_COMMENT_LIMIT, window_seconds=_COMMENT_WINDOW
    )

    if not within_limit:
        return CommentError.RATE_LIMITED

    comment_id = await ctx.account_comments.create(user_id, content)

    return UploadOutcome(comment_id=comment_id)


async def delete_account_comment(
    ctx: AbstractContext, session: Session, comment_id: int
) -> CommentError.OnSuccess[None]:
    comment = await ctx.account_comments.find_by_id(comment_id)

    if comment is None:
        return CommentError.NOT_FOUND

    user_id = session.user.id

    if comment.user_id != user_id and not await ctx.permissions.has(
        user_id, Permission.PROFILE_DELETE_ANY
    ):
        return CommentError.NOT_PERMITTED

    await ctx.account_comments.soft_delete(comment.id)

    if comment.user_id != user_id:
        await _audit.record(
            ctx, user_id, "delete", ModTarget.ACCOUNT_COMMENT, comment.id
        )

    return None


async def list_account_comments(
    ctx: AbstractContext, target_account_id: int, page: int, count: int
) -> CommentError.OnSuccess[AccountCommentsPayload]:
    size = _page_size(count)
    entries = await ctx.account_comments.list_by_user(target_account_id, page, size)
    total = await ctx.account_comments.count_by_user(target_account_id)

    return AccountCommentsPayload(
        comments=[_wire.account_comment(entry) for entry in entries],
        page=_page(total, page, size),
    )

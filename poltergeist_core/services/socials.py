from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus

from gdformat import objects
from gdformat.enums import FriendRequestState
from gdformat.enums import MessageState
from gdformat.enums import UserListType
from gdformat.requests import SendMessageRequest

from poltergeist_core.resources import Permission
from poltergeist_core.resources import User
from poltergeist_core.resources import UserStats
from poltergeist_core.services import _wire
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.services._common import is_error
from poltergeist_core.services.auth import Session
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_PAGE_SIZE = 10
_FRIEND_REQUEST_MESSAGE_MAX = 140
_SUBJECT_MAX = 35
_BODY_MAX = 200
_FRIEND_REQUEST_LIMIT = 20
_FRIEND_REQUEST_WINDOW = 600
_MESSAGE_LIMIT = 30
_MESSAGE_WINDOW = 600


class SocialError(ServiceError, StrEnum):
    NOT_FOUND = "not_found"
    SELF = "self"
    BLOCKED = "blocked"
    ALREADY_FRIENDS = "already_friends"
    NOT_FRIENDS = "not_friends"
    NOT_PERMITTED = "not_permitted"
    PRIVACY = "privacy"
    TOO_LONG = "too_long"
    RATE_LIMITED = "rate_limited"

    def service(self) -> str:
        return "socials"

    def status_code(self) -> int:
        match self:
            case SocialError.NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case SocialError.NOT_PERMITTED | SocialError.PRIVACY | SocialError.BLOCKED:
                return HTTPStatus.FORBIDDEN
            case SocialError.RATE_LIMITED:
                return HTTPStatus.TOO_MANY_REQUESTS
            case _:
                return HTTPStatus.BAD_REQUEST


@dataclass(frozen=True, slots=True)
class FriendRequestsPayload:
    requests: list[objects.FriendRequest]
    page: objects.Page


@dataclass(frozen=True, slots=True)
class MessagesPayload:
    messages: list[objects.Message]
    page: objects.Page


async def _users_with_stats(
    ctx: AbstractContext, user_ids: list[int]
) -> dict[int, tuple[User, UserStats]]:
    users = await ctx.users.find_many_by_ids(user_ids)
    stats = await ctx.stats.find_many_by_user_ids(user_ids)
    stats_by_id = {entry.user_id: entry for entry in stats}

    return {
        user.id: (user, stats_by_id[user.id])
        for user in users
        if user.id in stats_by_id
    }


async def _target(
    ctx: AbstractContext, session: Session, target_account_id: int
) -> SocialError.OnSuccess[User]:
    if target_account_id == session.user.id:
        return SocialError.SELF

    target = await ctx.users.find_by_id(target_account_id)

    if target is None:
        return SocialError.NOT_FOUND

    return target


async def send_friend_request(
    ctx: AbstractContext, session: Session, target_account_id: int, message: str
) -> SocialError.OnSuccess[None]:
    if not await ctx.permissions.has(session.user.id, Permission.FRIENDS_REQUEST):
        return SocialError.NOT_PERMITTED

    target = await _target(ctx, session, target_account_id)

    if is_error(target):
        return target

    if len(message) > _FRIEND_REQUEST_MESSAGE_MAX:
        return SocialError.TOO_LONG

    if target.friend_request_privacy is FriendRequestState.NONE:
        return SocialError.PRIVACY

    if await ctx.blocks.is_blocked_either_way(session.user.id, target.id):
        return SocialError.BLOCKED

    if await ctx.friendships.are_friends(session.user.id, target.id):
        return SocialError.ALREADY_FRIENDS

    within_limit = await ctx.rate_limits.hit(
        "friend_request",
        str(session.user.id),
        limit=_FRIEND_REQUEST_LIMIT,
        window_seconds=_FRIEND_REQUEST_WINDOW,
    )

    if not within_limit:
        return SocialError.RATE_LIMITED

    await ctx.friend_requests.create(session.user.id, target.id, message)

    return None


async def accept_friend_request(
    ctx: AbstractContext, session: Session, target_account_id: int
) -> SocialError.OnSuccess[None]:
    target = await _target(ctx, session, target_account_id)

    if is_error(target):
        return target

    request = await ctx.friend_requests.find_by_pair(target.id, session.user.id)

    if request is None:
        return SocialError.NOT_FOUND

    if await ctx.blocks.is_blocked_either_way(session.user.id, target.id):
        return SocialError.BLOCKED

    await ctx.friendships.create_pair(session.user.id, target.id)
    await ctx.friend_requests.delete_between(session.user.id, target.id)

    return None


async def delete_friend_requests(
    ctx: AbstractContext,
    session: Session,
    target_account_ids: tuple[int, ...],
    *,
    is_sender: bool,
) -> SocialError.OnSuccess[None]:
    targets = list(target_account_ids)

    if is_sender:
        await ctx.friend_requests.delete_outgoing(session.user.id, targets)
    else:
        await ctx.friend_requests.delete_incoming(session.user.id, targets)

    return None


async def read_friend_request(
    ctx: AbstractContext, session: Session, request_id: int
) -> SocialError.OnSuccess[None]:
    request = await ctx.friend_requests.find_by_id(request_id)

    if request is None or request.recipient_user_id != session.user.id:
        return SocialError.NOT_FOUND

    await ctx.friend_requests.mark_read(request_id)

    return None


async def list_friend_requests(
    ctx: AbstractContext, session: Session, page: int, *, sent: bool
) -> SocialError.OnSuccess[FriendRequestsPayload]:
    user_id = session.user.id

    if sent:
        entries = await ctx.friend_requests.list_outgoing(user_id, page, _PAGE_SIZE)
        total = await ctx.friend_requests.count_outgoing(user_id)
        peer_ids = [entry.recipient_user_id for entry in entries]
    else:
        entries = await ctx.friend_requests.list_incoming(user_id, page, _PAGE_SIZE)
        total = await ctx.friend_requests.count_incoming(user_id)
        peer_ids = [entry.sender_user_id for entry in entries]

    peers = await _users_with_stats(ctx, peer_ids)
    requests = []

    for entry, peer_id in zip(entries, peer_ids, strict=True):
        if peer_id not in peers:
            continue

        peer, stats = peers[peer_id]
        requests.append(_wire.friend_request(entry, peer, stats))

    return FriendRequestsPayload(
        requests=requests,
        page=objects.Page(total, page * _PAGE_SIZE, _PAGE_SIZE),
    )


async def remove_friend(
    ctx: AbstractContext, session: Session, target_account_id: int
) -> SocialError.OnSuccess[None]:
    target = await _target(ctx, session, target_account_id)

    if is_error(target):
        return target

    await ctx.friendships.delete_pair(session.user.id, target.id)

    return None


async def block(
    ctx: AbstractContext, session: Session, target_account_id: int
) -> SocialError.OnSuccess[None]:
    if not await ctx.permissions.has(session.user.id, Permission.USERS_BLOCK):
        return SocialError.NOT_PERMITTED

    target = await _target(ctx, session, target_account_id)

    if is_error(target):
        return target

    await ctx.blocks.create(session.user.id, target.id)
    await ctx.friendships.delete_pair(session.user.id, target.id)
    await ctx.friend_requests.delete_between(session.user.id, target.id)

    return None


async def unblock(
    ctx: AbstractContext, session: Session, target_account_id: int
) -> SocialError.OnSuccess[None]:
    target = await _target(ctx, session, target_account_id)

    if is_error(target):
        return target

    await ctx.blocks.delete(session.user.id, target.id)

    return None


async def user_list(
    ctx: AbstractContext, session: Session, list_type: UserListType
) -> SocialError.OnSuccess[list[objects.UserListEntry]]:
    user_id = session.user.id

    match list_type:
        case UserListType.FRIENDS:
            friendships = await ctx.friendships.list_by_user(user_id)
            peer_ids = [entry.friend_user_id for entry in friendships]
            unseen = {
                entry.friend_user_id for entry in friendships if entry.seen_at is None
            }
        case UserListType.BLOCKED:
            peer_ids = await ctx.blocks.list_blocked_ids(user_id)
            unseen = set()

    peers = await _users_with_stats(ctx, peer_ids)

    entries = [
        _wire.user_list_entry(*peers[peer_id], is_new=peer_id in unseen)
        for peer_id in peer_ids
        if peer_id in peers
    ]

    if unseen:
        await ctx.friendships.mark_seen(user_id)

    return entries


async def send_message(
    ctx: AbstractContext, session: Session, request: SendMessageRequest
) -> SocialError.OnSuccess[None]:
    if not await ctx.permissions.has(session.user.id, Permission.MESSAGES_SEND):
        return SocialError.NOT_PERMITTED

    target = await _target(ctx, session, request.target_account_id)

    if is_error(target):
        return target

    if len(request.subject) > _SUBJECT_MAX or len(request.body) > _BODY_MAX:
        return SocialError.TOO_LONG

    if await ctx.blocks.is_blocked_either_way(session.user.id, target.id):
        return SocialError.BLOCKED

    match target.message_privacy:
        case MessageState.NONE:
            return SocialError.PRIVACY
        case MessageState.FRIENDS:
            if not await ctx.friendships.are_friends(session.user.id, target.id):
                return SocialError.NOT_FRIENDS
        case MessageState.ALL:
            pass

    within_limit = await ctx.rate_limits.hit(
        "message",
        str(session.user.id),
        limit=_MESSAGE_LIMIT,
        window_seconds=_MESSAGE_WINDOW,
    )

    if not within_limit:
        return SocialError.RATE_LIMITED

    await ctx.messages.create(session.user.id, target.id, request.subject, request.body)

    return None


async def list_messages(
    ctx: AbstractContext, session: Session, page: int, *, sent: bool
) -> SocialError.OnSuccess[MessagesPayload]:
    user_id = session.user.id

    if sent:
        entries = await ctx.messages.list_outbox(user_id, page, _PAGE_SIZE)
        total = await ctx.messages.count_outbox(user_id)
        peer_ids = [entry.recipient_user_id for entry in entries]
    else:
        entries = await ctx.messages.list_inbox(user_id, page, _PAGE_SIZE)
        total = await ctx.messages.count_inbox(user_id)
        peer_ids = [entry.sender_user_id for entry in entries]

    peers = await ctx.users.find_many_by_ids(peer_ids)
    by_id = {peer.id: peer for peer in peers}

    messages = [
        _wire.message(entry, by_id[peer_id], outgoing=sent, include_body=False)
        for entry, peer_id in zip(entries, peer_ids, strict=True)
        if peer_id in by_id
    ]

    return MessagesPayload(
        messages=messages,
        page=objects.Page(total, page * _PAGE_SIZE, _PAGE_SIZE),
    )


async def download_message(
    ctx: AbstractContext, session: Session, message_id: int, *, is_sender: bool
) -> SocialError.OnSuccess[objects.Message]:
    entry = await ctx.messages.find_by_id(message_id)

    if entry is None:
        return SocialError.NOT_FOUND

    user_id = session.user.id

    if is_sender:
        if entry.sender_user_id != user_id or entry.sender_deleted_at is not None:
            return SocialError.NOT_FOUND

        peer_id = entry.recipient_user_id
    else:
        if entry.recipient_user_id != user_id or entry.recipient_deleted_at is not None:
            return SocialError.NOT_FOUND

        peer_id = entry.sender_user_id
        await ctx.messages.mark_read(entry.id)

    peer = await ctx.users.find_by_id(peer_id)

    if peer is None:
        return SocialError.NOT_FOUND

    return _wire.message(entry, peer, outgoing=is_sender, include_body=True)


async def delete_messages(
    ctx: AbstractContext,
    session: Session,
    message_ids: tuple[int, ...],
    *,
    is_sender: bool,
) -> SocialError.OnSuccess[None]:
    ids = list(message_ids)

    if is_sender:
        await ctx.messages.delete_for_sender(session.user.id, ids)
    else:
        await ctx.messages.delete_for_recipient(session.user.id, ids)

    return None

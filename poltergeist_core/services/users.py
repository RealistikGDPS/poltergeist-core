import dataclasses
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus

from gdformat import objects
from gdformat import requests
from gdformat.enums import FriendState
from gdformat.enums import LeaderboardStat
from gdformat.enums import LeaderboardType
from gdformat.enums import Length
from gdformat.enums import ModAccess
from gdformat.enums import ModLevel
from gdformat.requests import LeaderboardRequest
from gdformat.requests import UpdateStatsRequest

from poltergeist_core.resources import BanType
from poltergeist_core.resources import LeaderboardKind
from poltergeist_core.resources import Permission
from poltergeist_core.resources import Role
from poltergeist_core.resources import StatsUpdate
from poltergeist_core.resources import User
from poltergeist_core.resources import UserKind
from poltergeist_core.resources import UserStats
from poltergeist_core.services import _badges
from poltergeist_core.services import _wire
from poltergeist_core.services import anticheat
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.services.auth import Session
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_SEARCH_PAGE_SIZE = 10
_LEADERBOARD_MAX = 100
_STAT_MAX = 100_000_000
_ICON_MAX = 10_000
_COLOUR_MAX = 255
_STARS_PER_DEMON = 10


class UserError(ServiceError, StrEnum):
    NOT_FOUND = "not_found"
    NOT_PERMITTED = "not_permitted"
    INVALID_STATS = "invalid_stats"
    BAD_CHK = "bad_chk"

    def service(self) -> str:
        return "users"

    def status_code(self) -> int:
        match self:
            case UserError.NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case UserError.NOT_PERMITTED:
                return HTTPStatus.FORBIDDEN
            case UserError.INVALID_STATS | UserError.BAD_CHK:
                return HTTPStatus.BAD_REQUEST


@dataclass(frozen=True, slots=True)
class UserSearchPayload:
    users: list[objects.UserPreview]
    page: objects.Page


@dataclass(frozen=True, slots=True)
class PublicProfile:
    """A profile as anyone may see it: no friend state, no notifications."""

    user: User
    stats: UserStats
    ranks: dict[LeaderboardKind, int]
    roles: list[Role]
    mod_level: ModLevel


@dataclass(frozen=True, slots=True)
class LeaderboardEntry:
    rank: int
    user: User
    stats: UserStats


@dataclass(frozen=True, slots=True)
class LeaderboardPage:
    entries: list[LeaderboardEntry]
    page: int
    size: int
    total: int


async def _friend_state(
    ctx: AbstractContext, viewer: User, target: User
) -> tuple[FriendState, objects.IncomingFriendRequest | None]:
    if await ctx.friendships.are_friends(viewer.id, target.id):
        return FriendState.FRIENDS, None

    incoming = await ctx.friend_requests.find_by_pair(target.id, viewer.id)

    if incoming is not None:
        return FriendState.REQUEST_RECEIVED, objects.IncomingFriendRequest(
            id=incoming.id,
            message=incoming.message,
            age=_wire.age(incoming.created_at),
        )

    if await ctx.friend_requests.find_by_pair(viewer.id, target.id) is not None:
        return FriendState.REQUEST_SENT, None

    return FriendState.NONE, None


async def _notifications(ctx: AbstractContext, user_id: int) -> objects.Notifications:
    return objects.Notifications(
        messages=await ctx.messages.count_unread(user_id),
        friend_requests=await ctx.friend_requests.count_unread_incoming(user_id),
        friends=await ctx.friendships.count_unseen(user_id),
    )


async def profile(
    ctx: AbstractContext, session: Session, target_account_id: int
) -> UserError.OnSuccess[objects.User]:
    target = await ctx.users.find_by_id(target_account_id)

    if target is None:
        return UserError.NOT_FOUND

    stats = await ctx.stats.find_by_user_id(target.id)

    if stats is None:
        return UserError.NOT_FOUND

    is_self = target.id == session.user.id
    granted = await ctx.permissions.effective(target.id)
    rank = await ctx.leaderboards.rank(LeaderboardKind.STARS, target.id)
    friend_state = FriendState.NONE
    incoming = None
    notifications = None

    if is_self:
        notifications = await _notifications(ctx, target.id)
    else:
        friend_state, incoming = await _friend_state(ctx, session.user, target)

    return objects.User(
        name=target.username,
        user_id=target.id,
        account_id=target.id,
        stars=stats.stars,
        moons=stats.moons,
        demons=stats.demons,
        diamonds=stats.diamonds,
        secret_coins=stats.secret_coins,
        user_coins=stats.user_coins,
        creator_points=stats.creator_points,
        colour1=stats.colour1,
        colour2=stats.colour2,
        colour3=stats.colour3,
        icons=_wire.icon_set(stats),
        glow=stats.glow,
        global_rank=rank or 0,
        mod_level=_badges.mod_level(granted),
        privacy=_wire.privacy(target),
        socials=_wire.socials(target),
        custom=target.custom,
        demon_stats=_wire.demon_stats(stats),
        classic_stats=_wire.classic_stats(stats),
        platformer_stats=_wire.platformer_stats(stats),
        friend_state=friend_state,
        incoming_request=incoming,
        notifications=notifications,
    )


async def _previews(
    ctx: AbstractContext, users: list[User], ranks: dict[int, int]
) -> list[objects.UserPreview]:
    stats = await ctx.stats.find_many_by_user_ids([user.id for user in users])
    by_user = {entry.user_id: entry for entry in stats}

    return [
        _wire.user_preview(user, by_user[user.id], ranks.get(user.id, 0))
        for user in users
        if user.id in by_user
    ]


async def search(
    ctx: AbstractContext, query: str, page: int
) -> UserError.OnSuccess[UserSearchPayload]:
    query = query.strip()

    if query.isdecimal():
        user = await ctx.users.find_by_id(int(query))
        users = [] if user is None else [user]
        total = len(users)
    else:
        users = await ctx.users.search(query, page, _SEARCH_PAGE_SIZE)
        total = await ctx.users.count_search(query)

    ranks = await ctx.leaderboards.ranks(
        LeaderboardKind.STARS, [user.id for user in users]
    )

    return UserSearchPayload(
        users=await _previews(ctx, users, ranks),
        page=objects.Page(total, page * _SEARCH_PAGE_SIZE, _SEARCH_PAGE_SIZE),
    )


def _within_bounds(request: UpdateStatsRequest) -> bool:
    counters = (
        request.stars,
        request.moons,
        request.demons,
        request.diamonds,
        request.secret_coins,
        request.user_coins,
    )
    icons = request.icons

    icon_ids = (
        request.icon_id,
        icons.cube,
        icons.ship,
        icons.ball,
        icons.ufo,
        icons.wave,
        icons.robot,
        icons.spider,
        icons.swing,
        icons.jetpack,
        icons.explosion,
    )
    colours = (request.colour1, request.colour2, request.colour3)

    return (
        all(0 <= value <= _STAT_MAX for value in counters)
        and all(0 <= value <= _ICON_MAX for value in icon_ids)
        and all(-1 <= value <= _COLOUR_MAX for value in colours)
    )


async def _demon_breakdown(
    ctx: AbstractContext, request: UpdateStatsRequest
) -> dict[str, int]:
    """Counts completed online demons per tier from the level ids the client
    reports, so the profile breakdown cannot be forged independently."""

    levels = await ctx.levels.find_demon_ids(list(request.demon_level_ids))
    counts = dict.fromkeys(
        (
            "demons_easy",
            "demons_medium",
            "demons_hard",
            "demons_insane",
            "demons_extreme",
            "demons_easy_platformer",
            "demons_medium_platformer",
            "demons_hard_platformer",
            "demons_insane_platformer",
            "demons_extreme_platformer",
        ),
        0,
    )
    tiers = ("easy", "medium", "hard", "insane", "extreme")

    for level in levels:
        tier = tiers[level.difficulty - _STARS_PER_DEMON + len(tiers) - 1]
        suffix = "_platformer" if level.length is Length.PLATFORMER else ""
        counts[f"demons_{tier}{suffix}"] += 1

    return counts


async def update_stats(
    ctx: AbstractContext, session: Session, request: UpdateStatsRequest
) -> UserError.OnSuccess[int]:
    if not requests.verify_stats_chk(request):
        logger.warning(
            "Stats update failed the integrity check.",
            extra={
                "user_id": session.user.id,
                "binary_version": session.client.binary_version,
                "seed2": request.seed2,
                "stars": request.stars,
                "moons": request.moons,
                "demons": request.demons,
                "diamonds": request.diamonds,
                "secret_coins": request.secret_coins,
                "user_coins": request.user_coins,
                "icon_id": request.icon_id,
                "icon_type": int(request.icon_type),
                "icons": dataclasses.asdict(request.icons),
                "glow": request.glow,
                "demon_level_ids": list(request.demon_level_ids),
                "weekly_demons": request.weekly_demons,
                "gauntlet_demons": request.gauntlet_demons,
                "event_demons": request.event_demons,
                "classic": dataclasses.asdict(request.classic),
                "platformer": dataclasses.asdict(request.platformer),
            },
        )

        return UserError.BAD_CHK

    if not await ctx.permissions.has(session.user.id, Permission.STATS_UPDATE):
        return UserError.NOT_PERMITTED

    if not _within_bounds(request):
        return UserError.INVALID_STATS

    user_id = session.user.id
    icons = request.icons
    classic = request.classic
    platformer = request.platformer

    update = StatsUpdate(
        stars=request.stars,
        moons=request.moons,
        demons=request.demons,
        diamonds=request.diamonds,
        secret_coins=request.secret_coins,
        user_coins=request.user_coins,
        icon_type=request.icon_type,
        colour1=request.colour1,
        colour2=request.colour2,
        colour3=request.colour3,
        glow=request.glow,
        icon_cube=icons.cube,
        icon_ship=icons.ship,
        icon_ball=icons.ball,
        icon_ufo=icons.ufo,
        icon_wave=icons.wave,
        icon_robot=icons.robot,
        icon_spider=icons.spider,
        icon_swing=icons.swing,
        icon_jetpack=icons.jetpack,
        icon_explosion=icons.explosion,
        demons_weekly=request.weekly_demons,
        demons_gauntlet=request.gauntlet_demons,
        demons_event=request.event_demons,
        classic_auto=classic.auto,
        classic_easy=classic.easy,
        classic_normal=classic.normal,
        classic_hard=classic.hard,
        classic_harder=classic.harder,
        classic_insane=classic.insane,
        classic_daily=classic.daily,
        classic_gauntlet=classic.gauntlet,
        platformer_auto=platformer.auto,
        platformer_easy=platformer.easy,
        platformer_normal=platformer.normal,
        platformer_hard=platformer.hard,
        platformer_harder=platformer.harder,
        platformer_insane=platformer.insane,
        platformer_event=platformer.event,
        **await _demon_breakdown(ctx, request),
    )

    before = await ctx.stats.find_by_user_id(user_id)
    await ctx.stats.update(user_id, update)
    after = await ctx.stats.find_by_user_id(user_id)

    if before is not None and after is not None:
        await anticheat.note_stats(ctx, session.user, before, after)

    await ctx.users.touch_last_seen(user_id)
    await sync_leaderboards(ctx, user_id)

    return user_id


async def sync_leaderboards(ctx: AbstractContext, user_id: int) -> None:
    """Pushes a user's current statistics to the Redis rankings, or removes the
    user when they may not be ranked."""

    stats = await ctx.stats.find_by_user_id(user_id)

    if stats is None:
        return

    user = await ctx.users.find_by_id(user_id)
    player = user is not None and user.kind is UserKind.PLAYER
    ranked = await ctx.permissions.has(user_id, Permission.LEADERBOARD_RANK)
    banned = await ctx.bans.find_active(user_id, BanType.LEADERBOARD) is not None

    if not player or not ranked or banned:
        await ctx.leaderboards.remove(user_id)

        return

    creator_banned = await ctx.bans.find_active(user_id, BanType.CREATOR) is not None

    await ctx.leaderboards.set_scores(
        user_id,
        {
            LeaderboardKind.STARS: stats.stars,
            LeaderboardKind.MOONS: stats.moons,
            LeaderboardKind.DEMONS: stats.demons,
            LeaderboardKind.USER_COINS: stats.user_coins,
            LeaderboardKind.CREATOR_POINTS: 0
            if creator_banned
            else stats.creator_points,
        },
    )


def _kind(stat: LeaderboardStat) -> LeaderboardKind:
    match stat:
        case LeaderboardStat.STARS:
            return LeaderboardKind.STARS
        case LeaderboardStat.MOONS:
            return LeaderboardKind.MOONS
        case LeaderboardStat.DEMONS:
            return LeaderboardKind.DEMONS
        case LeaderboardStat.USER_COINS:
            return LeaderboardKind.USER_COINS


def _stat_value(stats: UserStats, stat: LeaderboardStat) -> int:
    match stat:
        case LeaderboardStat.STARS:
            return stats.stars
        case LeaderboardStat.MOONS:
            return stats.moons
        case LeaderboardStat.DEMONS:
            return stats.demons
        case LeaderboardStat.USER_COINS:
            return stats.user_coins


async def _ordered_previews(
    ctx: AbstractContext, user_ids: list[int], ranks: dict[int, int]
) -> list[objects.UserPreview]:
    users = await ctx.users.find_many_by_ids(user_ids)
    by_id = {user.id: user for user in users}
    ordered = [by_id[user_id] for user_id in user_ids if user_id in by_id]

    return await _previews(ctx, ordered, ranks)


async def _friends_leaderboard(
    ctx: AbstractContext, session: Session, stat: LeaderboardStat
) -> list[objects.UserPreview]:
    friend_ids = await ctx.friendships.list_friend_ids(session.user.id)
    members = await ctx.users.find_many_by_ids([session.user.id, *friend_ids])
    user_ids = [user.id for user in members if user.kind is UserKind.PLAYER]
    stats = await ctx.stats.find_many_by_user_ids(user_ids)
    stats.sort(key=lambda entry: _stat_value(entry, stat), reverse=True)
    ordered_ids = [entry.user_id for entry in stats[:_LEADERBOARD_MAX]]
    ranks = {user_id: index + 1 for index, user_id in enumerate(ordered_ids)}

    return await _ordered_previews(ctx, ordered_ids, ranks)


async def leaderboard(
    ctx: AbstractContext, session: Session, request: LeaderboardRequest
) -> UserError.OnSuccess[list[objects.UserPreview]]:
    count = min(max(request.count, 1), _LEADERBOARD_MAX)

    match request.leaderboard_type:
        case LeaderboardType.TOP:
            kind = _kind(request.stat)
            user_ids = await ctx.leaderboards.top(kind, count)
            ranks = {user_id: index + 1 for index, user_id in enumerate(user_ids)}
        case LeaderboardType.RELATIVE:
            kind = _kind(request.stat)
            user_ids = await ctx.leaderboards.around(kind, session.user.id, count)
            ranks = await ctx.leaderboards.ranks(kind, user_ids)
        case LeaderboardType.CREATORS:
            user_ids = await ctx.leaderboards.top(LeaderboardKind.CREATOR_POINTS, count)
            ranks = {user_id: index + 1 for index, user_id in enumerate(user_ids)}
        case LeaderboardType.FRIENDS:
            return await _friends_leaderboard(ctx, session, request.stat)

    return await _ordered_previews(ctx, user_ids, ranks)


async def mod_access(ctx: AbstractContext, session: Session) -> ModAccess:
    granted = await ctx.permissions.effective(session.user.id)

    return _badges.mod_access(granted)


async def find_by_reference(ctx: AbstractContext, reference: str) -> User | None:
    """Resolves a user from either an id or a username, as typed by a person."""

    reference = reference.strip()

    if reference.isdecimal():
        return await ctx.users.find_by_id(int(reference))

    return await ctx.users.find_by_username(reference)


async def public_profile(
    ctx: AbstractContext, user_id: int
) -> UserError.OnSuccess[PublicProfile]:
    user = await ctx.users.find_by_id(user_id)

    if user is None:
        return UserError.NOT_FOUND

    stats = await ctx.stats.find_by_user_id(user.id)

    if stats is None:
        return UserError.NOT_FOUND

    granted = await ctx.permissions.effective(user.id)

    return PublicProfile(
        user=user,
        stats=stats,
        ranks=await ctx.leaderboards.ranks_of(user.id),
        roles=await ctx.roles.list_by_user(user.id),
        mod_level=_badges.mod_level(granted),
    )


async def public_leaderboard(
    ctx: AbstractContext, kind: LeaderboardKind, *, page: int, size: int
) -> LeaderboardPage:
    size = min(max(size, 1), _LEADERBOARD_MAX)
    page = max(page, 0)
    user_ids = await ctx.leaderboards.page(kind, page, size)
    total = await ctx.leaderboards.count(kind)
    users = {user.id: user for user in await ctx.users.find_many_by_ids(user_ids)}
    stats = {
        entry.user_id: entry
        for entry in await ctx.stats.find_many_by_user_ids(user_ids)
    }
    entries = [
        LeaderboardEntry(
            rank=page * size + index + 1, user=users[user_id], stats=stats[user_id]
        )
        for index, user_id in enumerate(user_ids)
        if user_id in users and user_id in stats
    ]

    return LeaderboardPage(entries=entries, page=page, size=size, total=total)

from datetime import timedelta
from enum import StrEnum
from http import HTTPStatus

from gdformat import codes
from gdformat.enums import DemonDifficulty
from gdformat.enums import Difficulty
from gdformat.enums import Rating
from gdformat.enums import SendFeature
from gdformat.requests import RateDemonRequest
from gdformat.requests import SuggestStarsRequest

from poltergeist_core.resources import BanType
from poltergeist_core.resources import Level
from poltergeist_core.resources import ModTarget
from poltergeist_core.resources import Permission
from poltergeist_core.resources import UserKind
from poltergeist_core.services import _wire
from poltergeist_core.services import users
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.services._common import is_error
from poltergeist_core.services.auth import Session
from poltergeist_core.utilities import clock
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_STARS_MAX = 10
_DEMON_STARS = 10
_DEMON_OFFSET = 5
_REASON_MAX = 255


class ModerationError(ServiceError, StrEnum):
    NOT_MODERATOR = "not_moderator"
    NOT_PERMITTED = "not_permitted"
    NOT_FOUND = "not_found"
    INVALID = "invalid"
    TARGET_PROTECTED = "target_protected"

    def service(self) -> str:
        return "moderation"

    def status_code(self) -> int:
        match self:
            case ModerationError.NOT_MODERATOR | ModerationError.NOT_PERMITTED:
                return HTTPStatus.FORBIDDEN
            case ModerationError.TARGET_PROTECTED:
                return HTTPStatus.FORBIDDEN
            case ModerationError.NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case ModerationError.INVALID:
                return HTTPStatus.BAD_REQUEST

    def code(self) -> int:
        match self:
            case ModerationError.NOT_MODERATOR:
                return codes.ModeratorError.NOT_MODERATOR
            case _:
                return codes.ModeratorError.REJECTED


def _ban_permission(ban_type: BanType) -> Permission:
    match ban_type:
        case BanType.ACCOUNT:
            return Permission.USERS_BAN_ACCOUNT
        case BanType.COMMENT:
            return Permission.USERS_BAN_COMMENT
        case BanType.UPLOAD:
            return Permission.USERS_BAN_UPLOAD
        case BanType.LEADERBOARD:
            return Permission.USERS_BAN_LEADERBOARD
        case BanType.CREATOR:
            return Permission.USERS_BAN_CREATOR


async def refresh_creator_points(ctx: AbstractContext, user_id: int) -> None:
    points = await ctx.levels.creator_points_of(user_id)
    await ctx.stats.update_creator_points(user_id, points)
    await users.sync_leaderboards(ctx, user_id)


async def _permitted(
    ctx: AbstractContext, actor_user_id: int | None, permission: Permission
) -> bool:
    """`None` is the administration API, which is not subject to permissions."""

    if actor_user_id is None:
        return True

    return await ctx.permissions.has(actor_user_id, permission)


async def _log(
    ctx: AbstractContext,
    actor_user_id: int | None,
    action: str,
    target_type: ModTarget,
    target_id: int,
    details: dict[str, object] | None = None,
) -> None:
    if actor_user_id is not None:
        await ctx.mod_actions.create(
            actor_user_id, action, target_type, target_id, details
        )


async def rate_level(
    ctx: AbstractContext,
    *,
    actor_user_id: int | None,
    level_id: int,
    stars: int,
    feature: SendFeature | None,
    demon: DemonDifficulty | None,
) -> ModerationError.OnSuccess[Level]:
    """Sets a level's stars, and its feature tier when `feature` is given. Zero
    stars removes the rating entirely."""

    if not 0 <= stars <= _STARS_MAX:
        return ModerationError.INVALID

    level = await ctx.levels.find_by_id(level_id)

    if level is None:
        return ModerationError.NOT_FOUND

    if stars != level.stars and not await _permitted(
        ctx, actor_user_id, Permission.LEVELS_RATE
    ):
        return ModerationError.NOT_PERMITTED

    if feature is not None and not await _permitted(
        ctx, actor_user_id, Permission.LEVELS_FEATURE
    ):
        return ModerationError.NOT_PERMITTED

    if demon is None and level.difficulty.is_demon:
        demon_difficulty: Difficulty | None = level.difficulty
    elif demon is None:
        demon_difficulty = None
    else:
        demon_difficulty = Difficulty(_DEMON_OFFSET + demon)

    feature_order = level.feature_order
    rating = level.rating

    match feature:
        case None:
            pass
        case SendFeature.STAR:
            feature_order = 0
            rating = Rating.NONE
        case SendFeature.FEATURE:
            rating = Rating.NONE
        case SendFeature.EPIC:
            rating = Rating.EPIC
        case SendFeature.LEGENDARY:
            rating = Rating.LEGENDARY
        case SendFeature.MYTHIC:
            rating = Rating.MYTHIC

    if feature is not None and feature is not SendFeature.STAR and feature_order == 0:
        feature_order = await ctx.levels.next_feature_order()

    if stars == 0:
        feature_order = 0
        rating = Rating.NONE

    await ctx.levels.rate(
        level.id,
        stars=stars,
        difficulty=_wire.difficulty_for_stars(stars, demon_difficulty),
        coins_verified=stars > 0,
        feature_order=feature_order,
        rating=rating,
        rated_by_user_id=actor_user_id,
    )
    await ctx.suggestions.resolve_for_level(level.id)
    await refresh_creator_points(ctx, level.user_id)

    await _log(
        ctx,
        actor_user_id,
        "rate",
        ModTarget.LEVEL,
        level.id,
        {
            "stars": stars,
            "feature": None if feature is None else int(feature),
            "demon": None if demon is None else int(demon),
        },
    )
    updated = await ctx.levels.find_by_id(level.id)

    if updated is None:
        return ModerationError.NOT_FOUND

    return updated


async def suggest_stars(
    ctx: AbstractContext, session: Session, request: SuggestStarsRequest
) -> ModerationError.OnSuccess[None]:
    """Elder moderators rate directly; moderators queue a suggestion."""

    actor = session.user.id

    if await ctx.permissions.has(actor, Permission.LEVELS_RATE):
        rated = await rate_level(
            ctx,
            actor_user_id=actor,
            level_id=request.level_id,
            stars=request.stars,
            feature=request.feature,
            demon=None,
        )

        return rated if is_error(rated) else None

    if not await ctx.permissions.has(actor, Permission.LEVELS_SUGGEST):
        return ModerationError.NOT_MODERATOR

    if not 1 <= request.stars <= _STARS_MAX:
        return ModerationError.INVALID

    if await ctx.levels.find_by_id(request.level_id) is None:
        return ModerationError.NOT_FOUND

    await ctx.suggestions.create(
        request.level_id,
        actor,
        stars=request.stars,
        feature=request.feature,
        demon_difficulty=None,
    )

    return None


async def rate_demon(
    ctx: AbstractContext, session: Session, request: RateDemonRequest
) -> ModerationError.OnSuccess[int]:
    actor = session.user.id

    if not await ctx.permissions.has(actor, Permission.LEVELS_RATE_DEMON):
        return ModerationError.NOT_MODERATOR

    level = await ctx.levels.find_by_id(request.level_id)

    if level is None:
        return ModerationError.NOT_FOUND

    if not level.difficulty.is_demon:
        return ModerationError.INVALID

    await ctx.levels.set_difficulty(
        level.id, Difficulty(_DEMON_OFFSET + request.rating)
    )

    await ctx.mod_actions.create(
        actor, "rate_demon", ModTarget.LEVEL, level.id, {"rating": int(request.rating)}
    )

    return level.id


async def _outranks(
    ctx: AbstractContext, actor_user_id: int | None, target_user_id: int
) -> bool:
    if actor_user_id is None:
        return True

    if actor_user_id == target_user_id:
        return False

    actor_roles = await ctx.roles.list_by_user(actor_user_id)
    target_roles = await ctx.roles.list_by_user(target_user_id)
    actor_priority = max((role.priority for role in actor_roles), default=0)
    target_priority = max((role.priority for role in target_roles), default=0)

    return actor_priority > target_priority


async def ban(
    ctx: AbstractContext,
    *,
    actor_user_id: int | None,
    target_user_id: int,
    ban_type: BanType,
    days: int | None,
    reason: str,
) -> ModerationError.OnSuccess[int]:
    if not await _permitted(ctx, actor_user_id, _ban_permission(ban_type)):
        return ModerationError.NOT_PERMITTED

    if await ctx.users.find_by_id(target_user_id) is None:
        return ModerationError.NOT_FOUND

    if not await _outranks(ctx, actor_user_id, target_user_id):
        return ModerationError.TARGET_PROTECTED

    if days is not None and days <= 0:
        return ModerationError.INVALID

    expires_at = None if days is None else clock.now() + timedelta(days=days)

    ban_id = await ctx.bans.create(
        target_user_id,
        ban_type,
        reason=reason.strip()[:_REASON_MAX],
        issued_by_user_id=actor_user_id,
        expires_at=expires_at,
    )

    match ban_type:
        case BanType.ACCOUNT:
            await ctx.sessions.revoke(target_user_id)
            await ctx.web_sessions.revoke_all(target_user_id)
        case BanType.LEADERBOARD | BanType.CREATOR:
            await users.sync_leaderboards(ctx, target_user_id)
        case _:
            pass

    await _log(
        ctx,
        actor_user_id,
        "ban",
        ModTarget.USER,
        target_user_id,
        {"ban_id": ban_id, "type": ban_type.value, "days": days},
    )
    logger.info(
        "User banned.",
        extra={"user_id": target_user_id, "type": ban_type.value, "by": actor_user_id},
    )

    return ban_id


async def unban(
    ctx: AbstractContext,
    *,
    actor_user_id: int | None,
    target_user_id: int,
    ban_type: BanType,
) -> ModerationError.OnSuccess[int]:
    if not await _permitted(ctx, actor_user_id, Permission.USERS_UNBAN):
        return ModerationError.NOT_PERMITTED

    if await ctx.users.find_by_id(target_user_id) is None:
        return ModerationError.NOT_FOUND

    revoked = await ctx.bans.revoke_active(
        target_user_id, ban_type, revoked_by_user_id=actor_user_id
    )

    if ban_type in (BanType.LEADERBOARD, BanType.CREATOR):
        await users.sync_leaderboards(ctx, target_user_id)

    await _log(
        ctx,
        actor_user_id,
        "unban",
        ModTarget.USER,
        target_user_id,
        {"type": ban_type.value, "revoked": revoked},
    )

    return revoked


async def set_user_kind(
    ctx: AbstractContext,
    *,
    actor_user_id: int | None,
    target_user_id: int,
    kind: UserKind,
) -> ModerationError.OnSuccess[None]:
    if not await _permitted(ctx, actor_user_id, Permission.USERS_KIND_MANAGE):
        return ModerationError.NOT_PERMITTED

    target = await ctx.users.find_by_id(target_user_id)

    if target is None:
        return ModerationError.NOT_FOUND

    if not await _outranks(ctx, actor_user_id, target_user_id):
        return ModerationError.TARGET_PROTECTED

    if target.kind is kind:
        return None

    await ctx.users.update_kind(target_user_id, kind)
    await users.sync_leaderboards(ctx, target_user_id)
    await _log(
        ctx,
        actor_user_id,
        "kind",
        ModTarget.USER,
        target_user_id,
        {"kind": kind.value},
    )
    logger.info(
        "User kind changed.",
        extra={"user_id": target_user_id, "kind": kind.value, "by": actor_user_id},
    )

    return None

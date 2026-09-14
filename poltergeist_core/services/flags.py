from enum import StrEnum
from http import HTTPStatus

from poltergeist_core.resources import BanType
from poltergeist_core.resources import FlagKind
from poltergeist_core.resources import FlagStatus
from poltergeist_core.resources import ModTarget
from poltergeist_core.resources import Permission
from poltergeist_core.resources import StatsSource
from poltergeist_core.resources import UserFlag
from poltergeist_core.services import _audit
from poltergeist_core.services import moderation
from poltergeist_core.services import users
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.services._common import is_error
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)


class FlagError(ServiceError, StrEnum):
    NOT_PERMITTED = "not_permitted"
    NOT_FOUND = "not_found"
    ALREADY_RESOLVED = "already_resolved"
    NOT_RESTORABLE = "not_restorable"

    def service(self) -> str:
        return "flags"

    def status_code(self) -> int:
        match self:
            case FlagError.NOT_PERMITTED:
                return HTTPStatus.FORBIDDEN
            case FlagError.NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case FlagError.ALREADY_RESOLVED | FlagError.NOT_RESTORABLE:
                return HTTPStatus.CONFLICT


async def _open_flag(
    ctx: AbstractContext, actor_user_id: int, flag_id: int
) -> FlagError.OnSuccess[UserFlag]:
    if not await ctx.permissions.has(actor_user_id, Permission.FLAGS_REVIEW):
        return FlagError.NOT_PERMITTED

    flag = await ctx.flags.find_by_id(flag_id)

    if flag is None:
        return FlagError.NOT_FOUND

    if flag.status is not FlagStatus.OPEN:
        return FlagError.ALREADY_RESOLVED

    return flag


async def dismiss(
    ctx: AbstractContext, *, actor_user_id: int, flag_id: int
) -> FlagError.OnSuccess[None]:
    flag = await _open_flag(ctx, actor_user_id, flag_id)

    if is_error(flag):
        return flag

    await ctx.flags.resolve(
        flag.id, FlagStatus.DISMISSED, resolved_by_user_id=actor_user_id
    )

    await _audit.record(
        ctx,
        actor_user_id,
        "flag.dismiss",
        ModTarget.FLAG,
        flag.id,
        {"user_id": flag.user_id, "kind": flag.kind.value},
    )

    return None


async def ban_from_flag(
    ctx: AbstractContext,
    *,
    actor_user_id: int,
    flag_id: int,
    days: int | None,
    reason: str,
) -> FlagError.OnSuccess[int]:
    """A leaderboard ban of the flagged user, which also closes the flag."""

    flag = await _open_flag(ctx, actor_user_id, flag_id)

    if is_error(flag):
        return flag

    ban_id = await moderation.ban(
        ctx,
        actor_user_id=actor_user_id,
        target_user_id=flag.user_id,
        ban_type=BanType.LEADERBOARD,
        days=days,
        reason=reason,
    )

    if is_error(ban_id):
        return ban_id

    await ctx.flags.resolve(
        flag.id, FlagStatus.ACTIONED, resolved_by_user_id=actor_user_id
    )

    await _audit.record(
        ctx,
        actor_user_id,
        "flag.ban",
        ModTarget.FLAG,
        flag.id,
        {"user_id": flag.user_id, "kind": flag.kind.value, "ban_id": ban_id},
    )

    return ban_id


async def restore_stats(
    ctx: AbstractContext, *, actor_user_id: int, user_id: int, history_id: int
) -> FlagError.OnSuccess[None]:
    """Puts the counters back to a recorded snapshot; the icons and the
    completion counts the client reports stay as they are."""

    if not await ctx.permissions.has(actor_user_id, Permission.STATS_RESTORE):
        return FlagError.NOT_PERMITTED

    entry = await ctx.stats_history.find_by_id(history_id)

    if entry is None or entry.user_id != user_id:
        return FlagError.NOT_FOUND

    await ctx.stats.restore(user_id, entry.snapshot)
    await ctx.stats_history.create(user_id, StatsSource.RESTORE, entry.snapshot)
    await users.sync_leaderboards(ctx, user_id)

    await _audit.record(
        ctx,
        actor_user_id,
        "stats.restore",
        ModTarget.USER,
        user_id,
        {"history_id": history_id},
    )

    logger.info(
        "Stats restored.",
        extra={"user_id": user_id, "history_id": history_id, "by": actor_user_id},
    )

    return None


async def restore_from_flag(
    ctx: AbstractContext, *, actor_user_id: int, flag_id: int
) -> FlagError.OnSuccess[None]:
    """Restores the snapshot taken just before the update that raised a
    stats flag, and closes the flag."""

    flag = await _open_flag(ctx, actor_user_id, flag_id)

    if is_error(flag):
        return flag

    if flag.kind is not FlagKind.STATS_CEILING or flag.evidence is None:
        return FlagError.NOT_RESTORABLE

    previous = flag.evidence.get("previous_history_id")

    if not isinstance(previous, int):
        return FlagError.NOT_RESTORABLE

    restored = await restore_stats(
        ctx, actor_user_id=actor_user_id, user_id=flag.user_id, history_id=previous
    )

    if is_error(restored):
        return restored

    await ctx.flags.resolve(
        flag.id, FlagStatus.ACTIONED, resolved_by_user_id=actor_user_id
    )

    await _audit.record(
        ctx,
        actor_user_id,
        "flag.restore",
        ModTarget.FLAG,
        flag.id,
        {"user_id": flag.user_id, "history_id": previous},
    )

    return None

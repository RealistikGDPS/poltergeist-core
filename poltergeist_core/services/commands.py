from collections.abc import Sequence

from gdformat.enums import DemonDifficulty
from gdformat.enums import SendFeature
from gdformat.enums import TimelyType
from gdformat.enums import Visibility

from poltergeist_core import settings
from poltergeist_core.resources import BanType
from poltergeist_core.resources import Level
from poltergeist_core.resources import ModTarget
from poltergeist_core.resources import Permission
from poltergeist_core.resources import User
from poltergeist_core.services import moderation
from poltergeist_core.services import roles
from poltergeist_core.services import timely
from poltergeist_core.services import users
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.services._common import is_error
from poltergeist_core.services.auth import Session

_FEATURES = {
    "none": SendFeature.STAR,
    "star": SendFeature.STAR,
    "feature": SendFeature.FEATURE,
    "featured": SendFeature.FEATURE,
    "epic": SendFeature.EPIC,
    "legendary": SendFeature.LEGENDARY,
    "mythic": SendFeature.MYTHIC,
}
_DEMONS = {
    "easy": DemonDifficulty.EASY,
    "medium": DemonDifficulty.MEDIUM,
    "hard": DemonDifficulty.HARD,
    "insane": DemonDifficulty.INSANE,
    "extreme": DemonDifficulty.EXTREME,
}
_TIMELY = {
    "daily": TimelyType.DAILY,
    "weekly": TimelyType.WEEKLY,
    "event": TimelyType.EVENT,
}
_PERMANENT = ("perm", "permanent", "forever")
_COLOUR_MAX = 255
_HELP = (
    "help, ping, whois <user>, rate <stars> [feature], unrate, "
    "feature <none|feature|epic|legendary|mythic>, demon <tier>, daily|weekly|event "
    "[level], delete, unlist, relist, lock, unlock, move <user>, ban <user> <type> "
    "[days|perm] [reason], unban <user> <type>, colour <r> <g> <b>|none, "
    "role <user> <role>, unrole <user> <role>"
)


def _failed(error: ServiceError) -> str:
    return f"Failed: {error.resolve_name()}."


def _integer(value: str | None) -> int | None:
    if value is None or not value.lstrip("-").isdecimal():
        return None

    return int(value)


async def _level(
    ctx: AbstractContext, level_id: int, args: Sequence[str]
) -> Level | None:
    """The level named in the arguments, or the one being commented on."""

    given = _integer(args[0]) if args else None
    chosen = level_id if given is None else given

    if chosen <= 0:
        return None

    return await ctx.levels.find_by_id(chosen)


async def _user(ctx: AbstractContext, args: Sequence[str]) -> User | None:
    if not args:
        return None

    return await users.find_by_reference(ctx, args[0])


async def _rate(
    ctx: AbstractContext, session: Session, level_id: int, args: Sequence[str]
) -> str:
    level = await _level(ctx, level_id, ())
    stars = _integer(args[0]) if args else None

    if level is None or stars is None:
        return "Usage: rate <stars> [feature|epic|legendary|mythic]."

    feature = _FEATURES.get(args[1].lower()) if len(args) > 1 else None

    result = await moderation.rate_level(
        ctx,
        actor_user_id=session.user.id,
        level_id=level.id,
        stars=stars,
        feature=feature,
        demon=None,
    )

    if is_error(result):
        return _failed(result)

    return f"Rated {result.name} {result.stars} stars."


async def _unrate(ctx: AbstractContext, session: Session, level_id: int) -> str:
    level = await _level(ctx, level_id, ())

    if level is None:
        return "Use this command on a level."

    result = await moderation.rate_level(
        ctx,
        actor_user_id=session.user.id,
        level_id=level.id,
        stars=0,
        feature=None,
        demon=None,
    )

    if is_error(result):
        return _failed(result)

    return f"Removed the rating from {result.name}."


async def _feature(
    ctx: AbstractContext, session: Session, level_id: int, args: Sequence[str]
) -> str:
    level = await _level(ctx, level_id, ())
    feature = _FEATURES.get(args[0].lower()) if args else None

    if level is None or feature is None:
        return "Usage: feature <none|feature|epic|legendary|mythic>."

    if not level.is_rated:
        return "Rate the level first."

    result = await moderation.rate_level(
        ctx,
        actor_user_id=session.user.id,
        level_id=level.id,
        stars=level.stars,
        feature=feature,
        demon=None,
    )

    if is_error(result):
        return _failed(result)

    return f"Updated the feature tier of {result.name}."


async def _demon(
    ctx: AbstractContext, session: Session, level_id: int, args: Sequence[str]
) -> str:
    level = await _level(ctx, level_id, ())
    tier = _DEMONS.get(args[0].lower()) if args else None

    if level is None or tier is None:
        return "Usage: demon <easy|medium|hard|insane|extreme>."

    result = await moderation.rate_level(
        ctx,
        actor_user_id=session.user.id,
        level_id=level.id,
        stars=10,
        feature=None,
        demon=tier,
    )

    if is_error(result):
        return _failed(result)

    return f"{result.name} is now a {args[0].lower()} demon."


async def _schedule(
    ctx: AbstractContext,
    session: Session,
    level_id: int,
    args: Sequence[str],
    timely_type: TimelyType,
) -> str:
    level = await _level(ctx, level_id, args)

    if level is None:
        return "Use this command on a level or give a level id."

    result = await timely.schedule(
        ctx, actor_user_id=session.user.id, timely_type=timely_type, level_id=level.id
    )

    if is_error(result):
        return _failed(result)

    return f"Scheduled {level.name} as {timely_type.name.lower()} #{result.sequence}."


async def _edit_any(
    ctx: AbstractContext, session: Session, level_id: int
) -> Level | str:
    level = await _level(ctx, level_id, ())

    if level is None:
        return "Use this command on a level."

    is_owner = level.user_id == session.user.id

    if not is_owner and not await ctx.permissions.has(
        session.user.id, Permission.LEVELS_EDIT_ANY
    ):
        return "You may not edit this level."

    return level


async def _set_visibility(
    ctx: AbstractContext, session: Session, level_id: int, visibility: Visibility
) -> str:
    level = await _edit_any(ctx, session, level_id)

    if isinstance(level, str):
        return level

    await ctx.levels.set_visibility(level.id, visibility)

    await ctx.mod_actions.create(
        session.user.id,
        "visibility",
        ModTarget.LEVEL,
        level.id,
        {"visibility": int(visibility)},
    )

    return f"{level.name} is now {visibility.name.lower()}."


async def _set_lock(
    ctx: AbstractContext, session: Session, level_id: int, *, locked: bool
) -> str:
    level = await _edit_any(ctx, session, level_id)

    if isinstance(level, str):
        return level

    await ctx.levels.set_update_locked(level.id, locked=locked)

    return f"{level.name} is now {'locked' if locked else 'unlocked'} for updates."


async def _delete(ctx: AbstractContext, session: Session, level_id: int) -> str:
    level = await _level(ctx, level_id, ())

    if level is None:
        return "Use this command on a level."

    if level.user_id != session.user.id and not await ctx.permissions.has(
        session.user.id, Permission.LEVELS_DELETE_ANY
    ):
        return "You may not delete this level."

    await ctx.levels.soft_delete(level.id)
    await ctx.mod_actions.create(session.user.id, "delete", ModTarget.LEVEL, level.id)

    return f"Deleted {level.name}."


async def _move(
    ctx: AbstractContext, session: Session, level_id: int, args: Sequence[str]
) -> str:
    level = await _edit_any(ctx, session, level_id)

    if isinstance(level, str):
        return level

    target = await _user(ctx, args)

    if target is None:
        return "Usage: move <user>."

    await ctx.levels.transfer(level.id, target.id)
    await moderation.refresh_creator_points(ctx, level.user_id)
    await moderation.refresh_creator_points(ctx, target.id)

    await ctx.mod_actions.create(
        session.user.id, "move", ModTarget.LEVEL, level.id, {"user_id": target.id}
    )

    return f"Moved {level.name} to {target.username}."


def _ban_type(value: str | None) -> BanType | None:
    if value is None:
        return None

    try:
        return BanType(value.lower())
    except ValueError:
        return None


async def _ban(ctx: AbstractContext, session: Session, args: Sequence[str]) -> str:
    target = await _user(ctx, args)
    ban_type = _ban_type(args[1]) if len(args) > 1 else None

    if target is None or ban_type is None:
        return (
            "Usage: ban <user> <account|comment|upload|leaderboard|creator> "
            "[days|perm] [reason]."
        )

    days = None
    reason_start = 2

    if len(args) > 2:
        if args[2].lower() in _PERMANENT:
            reason_start = 3
        else:
            days = _integer(args[2])

            if days is not None:
                reason_start = 3

    result = await moderation.ban(
        ctx,
        actor_user_id=session.user.id,
        target_user_id=target.id,
        ban_type=ban_type,
        days=days,
        reason=" ".join(args[reason_start:]),
    )

    if is_error(result):
        return _failed(result)

    duration = "permanently" if days is None else f"for {days} days"

    return f"Banned {target.username} ({ban_type.value}) {duration}."


async def _unban(ctx: AbstractContext, session: Session, args: Sequence[str]) -> str:
    target = await _user(ctx, args)
    ban_type = _ban_type(args[1]) if len(args) > 1 else None

    if target is None or ban_type is None:
        return "Usage: unban <user> <account|comment|upload|leaderboard|creator>."

    result = await moderation.unban(
        ctx, actor_user_id=session.user.id, target_user_id=target.id, ban_type=ban_type
    )

    if is_error(result):
        return _failed(result)

    return f"Lifted {result} {ban_type.value} ban(s) from {target.username}."


async def _colour(ctx: AbstractContext, session: Session, args: Sequence[str]) -> str:
    if not await ctx.permissions.has(session.user.id, Permission.COMMENTS_COLOUR):
        return "You may not set a comment colour."

    if args and args[0].lower() == "none":
        await ctx.users.update_comment_colour(session.user.id, None)

        return "Comment colour cleared."

    channels = [_integer(value) for value in args[:3]]

    if len(channels) != 3 or any(
        c is None or not 0 <= c <= _COLOUR_MAX for c in channels
    ):
        return "Usage: colour <r> <g> <b> or colour none."

    red, green, blue = (int(c) for c in channels if c is not None)
    await ctx.users.update_comment_colour(
        session.user.id, (red << 16) | (green << 8) | blue
    )

    return f"Comment colour set to {red},{green},{blue}."


async def _role(
    ctx: AbstractContext, session: Session, args: Sequence[str], *, grant: bool
) -> str:
    target = await _user(ctx, args)

    if target is None or len(args) < 2:
        return f"Usage: {'role' if grant else 'unrole'} <user> <role>."

    if grant:
        result = await roles.assign(
            ctx,
            actor_user_id=session.user.id,
            target_user_id=target.id,
            role_name=args[1],
            expires_at=None,
        )
    else:
        result = await roles.revoke(
            ctx,
            actor_user_id=session.user.id,
            target_user_id=target.id,
            role_name=args[1],
        )

    if is_error(result):
        return _failed(result)

    verb = "Granted" if grant else "Revoked"

    return f"{verb} {result.name} for {target.username}."


async def _whois(ctx: AbstractContext, args: Sequence[str]) -> str:
    target = await _user(ctx, args)

    if target is None:
        return "Usage: whois <user>."

    granted = await ctx.roles.list_by_user(target.id)
    bans = await ctx.bans.list_active(target.id)
    role_names = ", ".join(role.name for role in granted) or "none"
    ban_names = ", ".join(ban.type.value for ban in bans) or "none"

    return (
        f"{target.username} (id {target.id}). Roles: {role_names}. Bans: {ban_names}."
    )


async def execute(
    ctx: AbstractContext, session: Session, content: str, level_id: int
) -> str:
    """Runs a command typed into a level comment and returns the reply shown to
    the player."""

    parts = content.removeprefix(settings.APP_COMMAND_PREFIX).split()

    if not parts:
        return _HELP

    name = parts[0].lower()
    args = parts[1:]

    match name:
        case "help":
            return _HELP
        case "ping":
            return f"{settings.APP_SERVER_NAME} is listening."
        case "whois":
            return await _whois(ctx, args)
        case "rate":
            return await _rate(ctx, session, level_id, args)
        case "unrate":
            return await _unrate(ctx, session, level_id)
        case "feature":
            return await _feature(ctx, session, level_id, args)
        case "demon":
            return await _demon(ctx, session, level_id, args)
        case "daily" | "weekly" | "event":
            return await _schedule(ctx, session, level_id, args, _TIMELY[name])
        case "delete":
            return await _delete(ctx, session, level_id)
        case "unlist":
            return await _set_visibility(ctx, session, level_id, Visibility.UNLISTED)
        case "relist":
            return await _set_visibility(ctx, session, level_id, Visibility.PUBLIC)
        case "lock":
            return await _set_lock(ctx, session, level_id, locked=True)
        case "unlock":
            return await _set_lock(ctx, session, level_id, locked=False)
        case "move":
            return await _move(ctx, session, level_id, args)
        case "ban":
            return await _ban(ctx, session, args)
        case "unban":
            return await _unban(ctx, session, args)
        case "colour" | "color":
            return await _colour(ctx, session, args)
        case "role":
            return await _role(ctx, session, args, grant=True)
        case "unrole":
            return await _role(ctx, session, args, grant=False)
        case _:
            return f"Unknown command {name}. Try {settings.APP_COMMAND_PREFIX}help."

import re
from datetime import datetime
from enum import StrEnum
from http import HTTPStatus

from gdformat.enums import ChestType
from gdformat.enums import QuestItem
from gdformat.enums import RewardItem
from gdformat.enums import Visibility

from poltergeist_core.resources import ModTarget
from poltergeist_core.resources import Permission
from poltergeist_core.resources import Role
from poltergeist_core.resources import Song
from poltergeist_core.services import auth
from poltergeist_core.services import leaderboards
from poltergeist_core.services import moderation
from poltergeist_core.services import songs
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.services._common import is_error
from poltergeist_core.services.auth import AuthError
from poltergeist_core.services.songs import SongError
from poltergeist_core.utilities import logging
from poltergeist_core.utilities import permissions

logger = logging.get_logger(__name__)

_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9 _-]{3,20}$")
_ROLE_NAME_PATTERN = re.compile(r"^[a-z0-9_]{2,32}$")
_REWARD_KEY_PATTERN = re.compile(r"^[a-z0-9_-]{2,64}$")
_SONG_NAME_LENGTH = 128
_ARTIST_NAME_LENGTH = 64
_SONG_URL_LENGTH = 512


class AdministrationError(ServiceError, StrEnum):
    NOT_PERMITTED = "not_permitted"
    NOT_FOUND = "not_found"
    INVALID = "invalid"
    TAKEN = "taken"

    def service(self) -> str:
        return "administration"

    def status_code(self) -> int:
        match self:
            case AdministrationError.NOT_PERMITTED:
                return HTTPStatus.FORBIDDEN
            case AdministrationError.NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case AdministrationError.INVALID:
                return HTTPStatus.BAD_REQUEST
            case AdministrationError.TAKEN:
                return HTTPStatus.CONFLICT


async def _require(
    ctx: AbstractContext, actor_user_id: int, permission: Permission
) -> AdministrationError | None:
    if await ctx.permissions.has(actor_user_id, permission):
        return None

    return AdministrationError.NOT_PERMITTED


async def rename_user(
    ctx: AbstractContext, *, actor_user_id: int, user_id: int, username: str
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.USERS_EDIT_ANY)

    if refused is not None:
        return refused

    username = username.strip()

    if not _USERNAME_PATTERN.match(username):
        return AdministrationError.INVALID

    user = await ctx.users.find_by_id(user_id)

    if user is None:
        return AdministrationError.NOT_FOUND

    existing = await ctx.users.find_by_username(username)

    if existing is not None and existing.id != user_id:
        return AdministrationError.TAKEN

    await ctx.users.update_username(user_id, username)
    await ctx.username_changes.create(
        user_id, user.username, username, changed_by_user_id=actor_user_id
    )

    await ctx.mod_actions.create(
        actor_user_id, "rename", ModTarget.USER, user_id, {"username": username}
    )

    return None


async def set_comment_colour(
    ctx: AbstractContext, *, actor_user_id: int, user_id: int, colour: int | None
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.USERS_EDIT_ANY)

    if refused is not None:
        return refused

    if await ctx.users.find_by_id(user_id) is None:
        return AdministrationError.NOT_FOUND

    await ctx.users.update_comment_colour(user_id, colour)

    await ctx.mod_actions.create(
        actor_user_id, "comment_colour", ModTarget.USER, user_id, {"colour": colour}
    )

    return None


async def revoke_sessions(
    ctx: AbstractContext, *, actor_user_id: int, user_id: int
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.USERS_EDIT_ANY)

    if refused is not None:
        return refused

    await ctx.sessions.revoke(user_id)
    await ctx.web_sessions.revoke_all(user_id)
    await ctx.permissions.invalidate(user_id)
    await ctx.mod_actions.create(
        actor_user_id, "revoke_sessions", ModTarget.USER, user_id
    )

    return None


async def set_password(
    ctx: AbstractContext, *, actor_user_id: int, user_id: int, password: str
) -> AdministrationError.OnSuccess[None]:
    """Resets a player's password and signs them out everywhere. Not logged
    in detail: the mod log never carries a credential."""

    refused = await _require(ctx, actor_user_id, Permission.USERS_EDIT_ANY)

    if refused is not None:
        return refused

    if not await moderation.outranks(ctx, actor_user_id, user_id):
        return AdministrationError.NOT_PERMITTED

    result = await auth.set_password(ctx, user_id, password)

    if is_error(result):
        match result:
            case AuthError.USER_NOT_FOUND:
                return AdministrationError.NOT_FOUND
            case _:
                return AdministrationError.INVALID

    await ctx.mod_actions.create(actor_user_id, "set_password", ModTarget.USER, user_id)

    return None


async def delete_level(
    ctx: AbstractContext, *, actor_user_id: int, level_id: int
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.LEVELS_DELETE_ANY)

    if refused is not None:
        return refused

    level = await ctx.levels.find_by_id(level_id)

    if level is None:
        return AdministrationError.NOT_FOUND

    await ctx.levels.soft_delete(level.id)
    await ctx.mod_actions.create(actor_user_id, "delete", ModTarget.LEVEL, level.id)

    return None


async def delete_comment(
    ctx: AbstractContext, *, actor_user_id: int, comment_id: int
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.COMMENTS_DELETE_ANY)

    if refused is not None:
        return refused

    comment = await ctx.comments.find_by_id(comment_id)

    if comment is None:
        return AdministrationError.NOT_FOUND

    await ctx.comments.soft_delete(comment.id)
    await ctx.mod_actions.create(actor_user_id, "delete", ModTarget.COMMENT, comment.id)

    return None


async def delete_account_comment(
    ctx: AbstractContext, *, actor_user_id: int, comment_id: int
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.PROFILE_DELETE_ANY)

    if refused is not None:
        return refused

    comment = await ctx.account_comments.find_by_id(comment_id)

    if comment is None:
        return AdministrationError.NOT_FOUND

    await ctx.account_comments.soft_delete(comment.id)
    await ctx.mod_actions.create(
        actor_user_id, "delete", ModTarget.ACCOUNT_COMMENT, comment.id
    )

    return None


async def set_level_visibility(
    ctx: AbstractContext, *, actor_user_id: int, level_id: int, visibility: Visibility
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.LEVELS_EDIT_ANY)

    if refused is not None:
        return refused

    if await ctx.levels.find_by_id(level_id) is None:
        return AdministrationError.NOT_FOUND

    await ctx.levels.set_visibility(level_id, visibility)

    await ctx.mod_actions.create(
        actor_user_id,
        "visibility",
        ModTarget.LEVEL,
        level_id,
        {"visibility": int(visibility)},
    )

    return None


async def set_level_locked(
    ctx: AbstractContext, *, actor_user_id: int, level_id: int, locked: bool
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.LEVELS_EDIT_ANY)

    if refused is not None:
        return refused

    if await ctx.levels.find_by_id(level_id) is None:
        return AdministrationError.NOT_FOUND

    await ctx.levels.set_update_locked(level_id, locked=locked)

    await ctx.mod_actions.create(
        actor_user_id, "lock", ModTarget.LEVEL, level_id, {"locked": locked}
    )

    return None


async def resolve_reports(
    ctx: AbstractContext, *, actor_user_id: int, level_id: int
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.LEVELS_VIEW_REPORTS)

    if refused is not None:
        return refused

    await ctx.reports.resolve_for_level(level_id, actor_user_id)
    await ctx.mod_actions.create(
        actor_user_id, "resolve_reports", ModTarget.LEVEL, level_id
    )

    return None


async def dismiss_suggestions(
    ctx: AbstractContext, *, actor_user_id: int, level_id: int
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.LEVELS_RATE)

    if refused is not None:
        return refused

    await ctx.suggestions.resolve_for_level(level_id)
    await ctx.mod_actions.create(
        actor_user_id, "dismiss_send", ModTarget.LEVEL, level_id
    )

    return None


async def remove_timely(
    ctx: AbstractContext, *, actor_user_id: int, timely_id: int
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.TIMELY_SCHEDULE)

    if refused is not None:
        return refused

    if await ctx.timely.find_by_id(timely_id) is None:
        return AdministrationError.NOT_FOUND

    await ctx.timely.soft_delete(timely_id)
    await ctx.mod_actions.create(
        actor_user_id, "remove", ModTarget.TIMELY_LEVEL, timely_id
    )

    return None


async def set_song_disabled(
    ctx: AbstractContext, *, actor_user_id: int, song_id: int, disabled: bool
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.SONGS_MANAGE)

    if refused is not None:
        return refused

    if await ctx.songs.find_by_id(song_id) is None:
        return AdministrationError.NOT_FOUND

    await ctx.songs.set_disabled(song_id, disabled=disabled)

    await ctx.mod_actions.create(
        actor_user_id, "disable", ModTarget.SONG, song_id, {"disabled": disabled}
    )

    return None


def _validate_song_fields(
    name: str, artist_name: str, url: str, size_bytes: int
) -> AdministrationError | None:
    if not name or not artist_name or not url.startswith("http") or size_bytes <= 0:
        return AdministrationError.INVALID

    if (
        len(name) > _SONG_NAME_LENGTH
        or len(artist_name) > _ARTIST_NAME_LENGTH
        or len(url) > _SONG_URL_LENGTH
    ):
        return AdministrationError.INVALID

    return None


async def create_song(
    ctx: AbstractContext,
    *,
    actor_user_id: int,
    name: str,
    artist_name: str,
    url: str,
    size_bytes: int,
) -> AdministrationError.OnSuccess[Song]:
    refused = await _require(ctx, actor_user_id, Permission.SONGS_MANAGE)

    if refused is not None:
        return refused

    name = name.strip()
    artist_name = artist_name.strip()
    url = url.strip()
    invalid = _validate_song_fields(name, artist_name, url, size_bytes)

    if invalid is not None:
        return invalid

    created = await songs.create_custom(
        ctx,
        name=name,
        artist_name=artist_name,
        url=url,
        size_bytes=size_bytes,
        uploaded_by_user_id=actor_user_id,
    )

    if is_error(created):
        match created:
            case SongError.NOT_FOUND:
                return AdministrationError.NOT_FOUND
            case _:
                return AdministrationError.INVALID

    await ctx.mod_actions.create(
        actor_user_id,
        "create",
        ModTarget.SONG,
        created.id,
        {"name": name, "artist": artist_name, "url": url, "size_bytes": size_bytes},
    )

    return created


async def update_song(
    ctx: AbstractContext,
    *,
    actor_user_id: int,
    song_id: int,
    name: str,
    artist_name: str,
    url: str,
    size_bytes: int,
) -> AdministrationError.OnSuccess[None]:
    """Applies to any known song, so a dead upstream URL can be replaced."""

    refused = await _require(ctx, actor_user_id, Permission.SONGS_MANAGE)

    if refused is not None:
        return refused

    name = name.strip()
    artist_name = artist_name.strip()
    url = url.strip()
    invalid = _validate_song_fields(name, artist_name, url, size_bytes)

    if invalid is not None:
        return invalid

    if await ctx.songs.find_by_id(song_id) is None:
        return AdministrationError.NOT_FOUND

    await ctx.songs.update(
        song_id,
        name=name,
        artist_id=await songs.artist_id_for(ctx, artist_name),
        size_bytes=size_bytes,
        url=url,
    )

    await ctx.mod_actions.create(
        actor_user_id,
        "update",
        ModTarget.SONG,
        song_id,
        {"name": name, "artist": artist_name, "url": url, "size_bytes": size_bytes},
    )

    return None


async def create_quest(
    ctx: AbstractContext,
    *,
    actor_user_id: int,
    item: QuestItem,
    amount: int,
    diamonds: int,
    name: str,
) -> AdministrationError.OnSuccess[int]:
    refused = await _require(ctx, actor_user_id, Permission.PACKS_MANAGE)

    if refused is not None:
        return refused

    if amount <= 0 or diamonds <= 0 or not name.strip():
        return AdministrationError.INVALID

    quest_id = await ctx.quests.create(
        item=item, amount=amount, diamonds=diamonds, name=name.strip()[:64]
    )
    await ctx.mod_actions.create(actor_user_id, "create", ModTarget.QUEST, quest_id)

    return quest_id


async def remove_quest(
    ctx: AbstractContext, *, actor_user_id: int, quest_id: int
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.PACKS_MANAGE)

    if refused is not None:
        return refused

    if await ctx.quests.find_by_id(quest_id) is None:
        return AdministrationError.NOT_FOUND

    await ctx.quests.soft_delete(quest_id)
    await ctx.mod_actions.create(actor_user_id, "remove", ModTarget.QUEST, quest_id)

    return None


async def create_secret_reward(
    ctx: AbstractContext,
    *,
    actor_user_id: int,
    reward_key: str,
    chest_type: ChestType,
    items: list[tuple[RewardItem, int]],
    max_claims: int | None,
    expires_at: datetime | None,
) -> AdministrationError.OnSuccess[int]:
    refused = await _require(ctx, actor_user_id, Permission.PACKS_MANAGE)

    if refused is not None:
        return refused

    reward_key = reward_key.strip().lower()

    if not _REWARD_KEY_PATTERN.match(reward_key) or not items:
        return AdministrationError.INVALID

    if any(amount <= 0 for _, amount in items):
        return AdministrationError.INVALID

    if await ctx.secret_rewards.find_by_key(reward_key) is not None:
        return AdministrationError.TAKEN

    reward_id = await ctx.secret_rewards.create(
        reward_key=reward_key,
        chest_type=chest_type,
        max_claims=max_claims,
        expires_at=expires_at,
    )

    for item, amount in items:
        await ctx.secret_rewards.add_item(reward_id, item, amount)

    await ctx.mod_actions.create(
        actor_user_id, "create", ModTarget.SECRET_REWARD, reward_id
    )

    return reward_id


async def remove_secret_reward(
    ctx: AbstractContext, *, actor_user_id: int, reward_id: int
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.PACKS_MANAGE)

    if refused is not None:
        return refused

    await ctx.secret_rewards.soft_delete(reward_id)
    await ctx.mod_actions.create(
        actor_user_id, "remove", ModTarget.SECRET_REWARD, reward_id
    )

    return None


def _valid_permissions(entries: list[str]) -> list[str] | None:
    cleaned = [entry.strip() for entry in entries if entry.strip()]

    if any(not permissions.is_valid_name(entry) for entry in cleaned):
        return None

    return sorted(set(cleaned))


async def create_role(
    ctx: AbstractContext,
    *,
    actor_user_id: int,
    name: str,
    description: str,
    priority: int,
    granted: list[str],
) -> AdministrationError.OnSuccess[Role]:
    refused = await _require(ctx, actor_user_id, Permission.USERS_ROLES_ASSIGN)

    if refused is not None:
        return refused

    name = name.strip().lower()
    valid = _valid_permissions(granted)

    if not _ROLE_NAME_PATTERN.match(name) or valid is None:
        return AdministrationError.INVALID

    if await ctx.roles.find_by_name(name) is not None:
        return AdministrationError.TAKEN

    role_id = await ctx.roles.create(name, description.strip()[:255], priority)
    await ctx.roles.replace_permissions(role_id, valid)
    await ctx.mod_actions.create(actor_user_id, "create", ModTarget.ROLE, role_id)
    role = await ctx.roles.find_by_id(role_id)

    if role is None:
        return AdministrationError.NOT_FOUND

    return role


async def update_role(
    ctx: AbstractContext,
    *,
    actor_user_id: int,
    role_id: int,
    name: str,
    description: str,
    priority: int,
    granted: list[str],
) -> AdministrationError.OnSuccess[None]:
    """Members' cached permissions are refreshed so the change applies at once."""

    refused = await _require(ctx, actor_user_id, Permission.USERS_ROLES_ASSIGN)

    if refused is not None:
        return refused

    role = await ctx.roles.find_by_id(role_id)

    if role is None:
        return AdministrationError.NOT_FOUND

    name = name.strip().lower()
    valid = _valid_permissions(granted)

    if not _ROLE_NAME_PATTERN.match(name) or valid is None:
        return AdministrationError.INVALID

    existing = await ctx.roles.find_by_name(name)

    if existing is not None and existing.id != role_id:
        return AdministrationError.TAKEN

    await ctx.roles.update(
        role_id, name=name, description=description.strip()[:255], priority=priority
    )
    await ctx.roles.replace_permissions(role_id, valid)

    for member_id in await ctx.roles.list_member_ids(role_id):
        await ctx.permissions.invalidate(member_id)

    await ctx.mod_actions.create(
        actor_user_id, "update", ModTarget.ROLE, role_id, {"permissions": valid}
    )

    return None


async def remove_role(
    ctx: AbstractContext, *, actor_user_id: int, role_id: int
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.USERS_ROLES_ASSIGN)

    if refused is not None:
        return refused

    if await ctx.roles.find_by_id(role_id) is None:
        return AdministrationError.NOT_FOUND

    members = await ctx.roles.list_member_ids(role_id)
    await ctx.roles.soft_delete(role_id)

    for member_id in members:
        await ctx.permissions.invalidate(member_id)

    await ctx.mod_actions.create(actor_user_id, "remove", ModTarget.ROLE, role_id)

    return None


async def remove_map_pack(
    ctx: AbstractContext, *, actor_user_id: int, pack_id: int
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.PACKS_MANAGE)

    if refused is not None:
        return refused

    if await ctx.map_packs.find_by_id(pack_id) is None:
        return AdministrationError.NOT_FOUND

    await ctx.map_packs.soft_delete(pack_id)
    await ctx.mod_actions.create(actor_user_id, "remove", ModTarget.MAP_PACK, pack_id)

    return None


async def remove_gauntlet(
    ctx: AbstractContext, *, actor_user_id: int, gauntlet_id: int
) -> AdministrationError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.PACKS_MANAGE)

    if refused is not None:
        return refused

    if await ctx.gauntlets.find_by_id(gauntlet_id) is None:
        return AdministrationError.NOT_FOUND

    await ctx.gauntlets.soft_delete(gauntlet_id)
    await ctx.mod_actions.create(
        actor_user_id, "remove", ModTarget.GAUNTLET, gauntlet_id
    )

    return None


async def rebuild_leaderboards(
    ctx: AbstractContext, *, actor_user_id: int
) -> AdministrationError.OnSuccess[int]:
    refused = await _require(ctx, actor_user_id, Permission.ADMIN_MAINTENANCE)

    if refused is not None:
        return refused

    total = await leaderboards.rebuild(ctx)
    await ctx.mod_actions.create(
        actor_user_id, "rebuild_leaderboards", ModTarget.SERVER, 0, {"users": total}
    )

    return total

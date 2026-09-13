from datetime import datetime
from enum import StrEnum
from http import HTTPStatus

from poltergeist_core.resources import ModTarget
from poltergeist_core.resources import Permission
from poltergeist_core.resources import Role
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)


class RoleError(ServiceError, StrEnum):
    NOT_FOUND = "not_found"
    USER_NOT_FOUND = "user_not_found"
    NOT_PERMITTED = "not_permitted"
    ROLE_TOO_HIGH = "role_too_high"

    def service(self) -> str:
        return "roles"

    def status_code(self) -> int:
        match self:
            case RoleError.NOT_FOUND | RoleError.USER_NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case RoleError.NOT_PERMITTED | RoleError.ROLE_TOO_HIGH:
                return HTTPStatus.FORBIDDEN


async def _may_manage(ctx: AbstractContext, actor_user_id: int, role: Role) -> bool:
    """A person may only hand out roles below their own highest role."""

    actor_roles = await ctx.roles.list_by_user(actor_user_id)
    highest = max((entry.priority for entry in actor_roles), default=0)

    return role.priority < highest


async def assign(
    ctx: AbstractContext,
    *,
    actor_user_id: int,
    target_user_id: int,
    role_name: str,
    expires_at: datetime | None,
) -> RoleError.OnSuccess[Role]:
    if not await ctx.permissions.has(actor_user_id, Permission.USERS_ROLES_ASSIGN):
        return RoleError.NOT_PERMITTED

    role = await ctx.roles.find_by_name(role_name.strip().lower())

    if role is None:
        return RoleError.NOT_FOUND

    if await ctx.users.find_by_id(target_user_id) is None:
        return RoleError.USER_NOT_FOUND

    if not await _may_manage(ctx, actor_user_id, role):
        return RoleError.ROLE_TOO_HIGH

    await ctx.roles.assign(
        target_user_id, role.id, granted_by_user_id=actor_user_id, expires_at=expires_at
    )
    await ctx.permissions.invalidate(target_user_id)

    await ctx.mod_actions.create(
        actor_user_id,
        "assign",
        ModTarget.ROLE,
        role.id,
        {"user_id": target_user_id},
    )

    logger.info(
        "Role assigned.",
        extra={"user_id": target_user_id, "role": role.name, "by": actor_user_id},
    )

    return role


async def revoke(
    ctx: AbstractContext,
    *,
    actor_user_id: int,
    target_user_id: int,
    role_name: str,
) -> RoleError.OnSuccess[Role]:
    if not await ctx.permissions.has(actor_user_id, Permission.USERS_ROLES_REVOKE):
        return RoleError.NOT_PERMITTED

    role = await ctx.roles.find_by_name(role_name.strip().lower())

    if role is None:
        return RoleError.NOT_FOUND

    if not await _may_manage(ctx, actor_user_id, role):
        return RoleError.ROLE_TOO_HIGH

    if not await ctx.roles.revoke(target_user_id, role.id):
        return RoleError.USER_NOT_FOUND

    await ctx.permissions.invalidate(target_user_id)

    await ctx.mod_actions.create(
        actor_user_id,
        "revoke",
        ModTarget.ROLE,
        role.id,
        {"user_id": target_user_id},
    )

    return role


async def list_for_user(ctx: AbstractContext, user_id: int) -> list[Role]:
    return await ctx.roles.list_by_user(user_id)

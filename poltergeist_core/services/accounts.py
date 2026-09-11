from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus

from gdformat import codes
from gdformat import objects
from gdformat.requests import BackupRequest
from gdformat.requests import UpdateSettingsRequest

from poltergeist_core import settings
from poltergeist_core.resources import Permission
from poltergeist_core.services import _wire
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.services.auth import Session
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_SOCIAL_MAX = 64
_GAME_MANAGER_KEY = "saves/{user_id}/game_manager.dat"
_LOCAL_LEVELS_KEY = "saves/{user_id}/local_levels.dat"


class AccountError(ServiceError, StrEnum):
    NOT_PERMITTED = "not_permitted"
    SAVE_TOO_LARGE = "save_too_large"
    SAVE_MISSING = "save_missing"

    def service(self) -> str:
        return "accounts"

    def status_code(self) -> int:
        match self:
            case AccountError.NOT_PERMITTED:
                return HTTPStatus.FORBIDDEN
            case AccountError.SAVE_TOO_LARGE:
                return HTTPStatus.CONTENT_TOO_LARGE
            case AccountError.SAVE_MISSING:
                return HTTPStatus.NOT_FOUND

    def code(self) -> int:
        match self:
            case AccountError.SAVE_TOO_LARGE:
                return codes.SaveError.TOO_LARGE
            case AccountError.NOT_PERMITTED:
                return codes.SaveError.BAD_LOGIN
            case AccountError.SAVE_MISSING:
                return codes.SaveError.GENERIC


@dataclass(frozen=True, slots=True)
class SyncPayload:
    game_manager: str
    local_levels: str
    game_version: int
    binary_version: int
    rated_levels: list[tuple[int, int]]
    map_packs: list[objects.MapPack]


def account_url() -> str:
    return settings.APP_PUBLIC_URL


async def update_settings(
    ctx: AbstractContext, session: Session, request: UpdateSettingsRequest
) -> AccountError.OnSuccess[None]:
    socials = request.socials

    await ctx.users.update_settings(
        session.user.id,
        message_privacy=request.privacy.messages,
        friend_request_privacy=request.privacy.friend_requests,
        comment_history_privacy=request.privacy.comment_history,
        youtube=socials.youtube[:_SOCIAL_MAX],
        twitter=socials.twitter[:_SOCIAL_MAX],
        twitch=socials.twitch[:_SOCIAL_MAX],
        discord=socials.discord[:_SOCIAL_MAX],
        instagram=socials.instagram[:_SOCIAL_MAX],
        tiktok=socials.tiktok[:_SOCIAL_MAX],
        custom=request.custom[:_SOCIAL_MAX],
    )

    return None


async def backup(
    ctx: AbstractContext, session: Session, request: BackupRequest
) -> AccountError.OnSuccess[None]:
    if not await ctx.permissions.has(session.user.id, Permission.SAVES_BACKUP):
        return AccountError.NOT_PERMITTED

    game_manager = request.game_manager.encode()
    local_levels = request.local_levels.encode()

    if len(game_manager) + len(local_levels) > settings.APP_SAVE_MAX_BYTES:
        return AccountError.SAVE_TOO_LARGE

    user_id = session.user.id
    await ctx.storage.save(_GAME_MANAGER_KEY.format(user_id=user_id), game_manager)
    await ctx.storage.save(_LOCAL_LEVELS_KEY.format(user_id=user_id), local_levels)

    await ctx.saves.upsert(
        user_id,
        game_version=request.client.game_version,
        binary_version=request.client.binary_version,
        game_manager_bytes=len(game_manager),
        local_levels_bytes=len(local_levels),
    )

    logger.info(
        "Account backed up.",
        extra={"user_id": user_id, "bytes": len(game_manager) + len(local_levels)},
    )

    return None


async def sync(
    ctx: AbstractContext, session: Session
) -> AccountError.OnSuccess[SyncPayload]:
    user_id = session.user.id
    save = await ctx.saves.find_by_user_id(user_id)

    if save is None:
        return AccountError.SAVE_MISSING

    game_manager = await ctx.storage.load(_GAME_MANAGER_KEY.format(user_id=user_id))
    local_levels = await ctx.storage.load(_LOCAL_LEVELS_KEY.format(user_id=user_id))

    if game_manager is None or local_levels is None:
        logger.error(
            "Save metadata exists without stored data.", extra={"user_id": user_id}
        )

        return AccountError.SAVE_MISSING

    rated = await ctx.levels.list_rated()
    packs = await ctx.map_packs.list_all()
    pack_levels = await ctx.map_packs.list_level_ids_many([pack.id for pack in packs])

    return SyncPayload(
        game_manager=game_manager.decode(),
        local_levels=local_levels.decode(),
        game_version=save.game_version,
        binary_version=save.binary_version,
        rated_levels=[(level.id, level.stars) for level in rated],
        map_packs=[_wire.map_pack(pack, pack_levels[pack.id]) for pack in packs],
    )

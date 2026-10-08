from enum import StrEnum
from http import HTTPStatus

from poltergeist_core.resources import ModTarget
from poltergeist_core.resources import Permission
from poltergeist_core.resources import ServerSettings
from poltergeist_core.resources import ServerSettingsUpdated
from poltergeist_core.services import _audit
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError

_URL_MAX = 512


class ServerSettingsError(ServiceError, StrEnum):
    NOT_PERMITTED = "not_permitted"
    INVALID = "invalid"

    def service(self) -> str:
        return "server_settings"

    def status_code(self) -> int:
        match self:
            case ServerSettingsError.NOT_PERMITTED:
                return HTTPStatus.FORBIDDEN
            case ServerSettingsError.INVALID:
                return HTTPStatus.BAD_REQUEST


def _stringify(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"

    return str(value)


def _valid_url(url: str) -> bool:
    """Empty hides the link; otherwise HTTPS or a path on this site."""

    if url == "":
        return True

    if len(url) > _URL_MAX:
        return False

    return url.startswith("https://") or (
        url.startswith("/") and not url.startswith("//")
    )


async def current(ctx: AbstractContext) -> ServerSettings:
    return await ctx.server_settings.load()


async def update(
    ctx: AbstractContext, *, actor_user_id: int, settings: ServerSettings
) -> ServerSettingsError.OnSuccess[ServerSettings]:
    if not await ctx.permissions.has(actor_user_id, Permission.ADMIN_SETTINGS):
        return ServerSettingsError.NOT_PERMITTED

    links = (
        settings.download_pc_url,
        settings.download_android_url,
        settings.download_ios_url,
        settings.discord_url,
    )

    if not all(_valid_url(link) for link in links):
        return ServerSettingsError.INVALID

    allowances = (
        settings.official_stars,
        settings.official_moons,
        settings.official_demons,
        settings.official_secret_coins,
    )

    if any(value < 0 for value in allowances):
        return ServerSettingsError.INVALID

    if settings.level_reupload_daily_limit < 1 or settings.reupload_bot_user_id < 0:
        return ServerSettingsError.INVALID

    if settings.song_upload_daily_limit < 1:
        return ServerSettingsError.INVALID

    if settings.demon_list_daily_submissions < 1 or settings.demon_list_top_points < 1:
        return ServerSettingsError.INVALID

    if not 1 <= settings.demon_list_decay_percent <= 100:
        return ServerSettingsError.INVALID

    if (
        settings.reupload_bot_user_id
        and await ctx.users.find_by_id(settings.reupload_bot_user_id) is None
    ):
        return ServerSettingsError.INVALID

    before = (await ctx.server_settings.load()).model_dump()

    changes = {
        key: value
        for key, value in settings.model_dump().items()
        if before[key] != value
    }

    if not changes:
        return settings

    for key, value in changes.items():
        await ctx.server_settings.set(
            key, _stringify(value), updated_by_user_id=actor_user_id
        )

    await ctx.server_settings.invalidate()
    await _audit.record(ctx, actor_user_id, "settings", ModTarget.SERVER, 0, changes)

    await ctx.events.publish(
        ServerSettingsUpdated(changes=changes, actor_user_id=actor_user_id)
    )

    return settings

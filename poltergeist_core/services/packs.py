from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus

from gdformat import objects
from gdformat.enums import MapPackDifficulty

from poltergeist_core.resources import ModTarget
from poltergeist_core.services import _wire
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError

_PAGE_SIZE = 10
_GAUNTLET_SIZE = 5
_GAUNTLET_ID_MAX = 255


class PackError(ServiceError, StrEnum):
    NOT_FOUND = "not_found"
    INVALID = "invalid"

    def service(self) -> str:
        return "packs"

    def status_code(self) -> int:
        match self:
            case PackError.NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case PackError.INVALID:
                return HTTPStatus.BAD_REQUEST


@dataclass(frozen=True, slots=True)
class MapPacksPayload:
    packs: list[objects.MapPack]
    page: objects.Page


async def map_packs(
    ctx: AbstractContext, page: int
) -> PackError.OnSuccess[MapPacksPayload]:
    packs = await ctx.map_packs.list_page(page, _PAGE_SIZE)
    total = await ctx.map_packs.count()
    level_ids = await ctx.map_packs.list_level_ids_many([pack.id for pack in packs])

    return MapPacksPayload(
        packs=[_wire.map_pack(pack, level_ids[pack.id]) for pack in packs],
        page=objects.Page(total, page * _PAGE_SIZE, _PAGE_SIZE),
    )


async def gauntlets(ctx: AbstractContext) -> list[objects.Gauntlet]:
    entries = await ctx.gauntlets.list_all()

    return [_wire.gauntlet(entry) for entry in entries if entry.level_ids]


async def _known_level_ids(ctx: AbstractContext, level_ids: list[int]) -> list[int]:
    known = {level.id for level in await ctx.levels.find_many_by_ids(level_ids)}

    return [level_id for level_id in dict.fromkeys(level_ids) if level_id in known]


async def create_map_pack(
    ctx: AbstractContext,
    *,
    actor_user_id: int | None,
    name: str,
    level_ids: list[int],
    stars: int,
    coins: int,
    difficulty: MapPackDifficulty,
    text_colour: int,
    bar_colour: int,
) -> PackError.OnSuccess[int]:
    levels = await _known_level_ids(ctx, level_ids)

    if not name.strip() or not levels:
        return PackError.INVALID

    pack_id = await ctx.map_packs.create(
        name=name.strip(),
        stars=stars,
        coins=coins,
        difficulty=difficulty,
        text_colour=text_colour,
        bar_colour=bar_colour,
    )
    await ctx.map_packs.replace_levels(pack_id, levels)

    if actor_user_id is not None:
        await ctx.mod_actions.create(
            actor_user_id, "create", ModTarget.MAP_PACK, pack_id
        )

    return pack_id


async def set_gauntlet(
    ctx: AbstractContext,
    *,
    actor_user_id: int | None,
    gauntlet_id: int,
    level_ids: list[int],
) -> PackError.OnSuccess[None]:
    levels = await _known_level_ids(ctx, level_ids)

    if not 1 <= gauntlet_id <= _GAUNTLET_ID_MAX or len(levels) != _GAUNTLET_SIZE:
        return PackError.INVALID

    await ctx.gauntlets.upsert(gauntlet_id, levels)

    if actor_user_id is not None:
        await ctx.mod_actions.create(
            actor_user_id, "set", ModTarget.GAUNTLET, gauntlet_id
        )

    return None

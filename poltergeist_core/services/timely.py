from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from http import HTTPStatus

from gdformat import objects
from gdformat.enums import ChestType
from gdformat.enums import RewardItem
from gdformat.enums import TimelyType

from poltergeist_core.resources import ModTarget
from poltergeist_core.resources import Permission
from poltergeist_core.resources import TimelyLevel
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.utilities import clock

_DURATIONS = {
    TimelyType.DAILY: timedelta(days=1),
    TimelyType.WEEKLY: timedelta(days=7),
    TimelyType.EVENT: timedelta(days=1),
}
_EVENT_REWARDS = ((RewardItem.ORBS, 500), (RewardItem.DIAMONDS, 10))


class TimelyError(ServiceError, StrEnum):
    NOT_FOUND = "not_found"
    NOT_PERMITTED = "not_permitted"
    LEVEL_NOT_FOUND = "level_not_found"

    def service(self) -> str:
        return "timely"

    def status_code(self) -> int:
        match self:
            case TimelyError.NOT_FOUND | TimelyError.LEVEL_NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case TimelyError.NOT_PERMITTED:
                return HTTPStatus.FORBIDDEN


@dataclass(frozen=True, slots=True)
class CurrentTimely:
    timely: TimelyLevel
    seconds_left: int
    rewards: tuple[objects.RewardStack, ...]


async def current(
    ctx: AbstractContext, timely_type: TimelyType
) -> TimelyError.OnSuccess[CurrentTimely]:
    entry = await ctx.timely.find_current(timely_type)

    if entry is None:
        return TimelyError.NOT_FOUND

    rewards: tuple[objects.RewardStack, ...] = ()

    if timely_type is TimelyType.EVENT:
        rewards = tuple(
            objects.RewardStack(reward.item, reward.amount)
            for reward in await ctx.timely.list_rewards(entry.id)
        )

    return CurrentTimely(
        timely=entry,
        seconds_left=clock.seconds_until(entry.ends_at),
        rewards=rewards,
    )


async def schedule(
    ctx: AbstractContext,
    *,
    actor_user_id: int,
    timely_type: TimelyType,
    level_id: int,
) -> TimelyError.OnSuccess[TimelyLevel]:
    """Appends the level to the queue: it starts when the previous entry of
    the same type ends, or right away when nothing valid is being served."""

    if not await ctx.permissions.has(actor_user_id, Permission.TIMELY_SCHEDULE):
        return TimelyError.NOT_PERMITTED

    level = await ctx.levels.find_by_id(level_id)

    if level is None:
        return TimelyError.LEVEL_NOT_FOUND

    last = await ctx.timely.find_last(timely_type)
    current = await ctx.timely.find_current(timely_type)
    now = clock.now()
    starts_at = now if last is None or current is None else max(last.ends_at, now)
    sequence = 1 if last is None else last.sequence + 1
    chest_type = ChestType.EVENT if timely_type is TimelyType.EVENT else None

    timely_id = await ctx.timely.create(
        timely_type=timely_type,
        sequence=sequence,
        level_id=level.id,
        starts_at=starts_at,
        ends_at=starts_at + _DURATIONS[timely_type],
        chest_type=chest_type,
        scheduled_by_user_id=actor_user_id,
    )

    if timely_type is TimelyType.EVENT:
        for item, amount in _EVENT_REWARDS:
            await ctx.timely.add_reward(timely_id, item, amount)

    await ctx.mod_actions.create(
        actor_user_id,
        "schedule",
        ModTarget.TIMELY_LEVEL,
        timely_id,
        {"type": int(timely_type), "level_id": level.id},
    )

    entry = await ctx.timely.find_by_id(timely_id)

    if entry is None:
        return TimelyError.NOT_FOUND

    return entry


async def resolve_wire_id(ctx: AbstractContext, wire_id: int) -> TimelyLevel | None:
    """Maps the client's timely id (sequence plus type offset) back to a row."""

    if wire_id <= 0:
        return None

    for timely_type in reversed(TimelyType):
        if wire_id > timely_type.id_offset:
            return await ctx.timely.find_by_sequence(
                timely_type, wire_id - timely_type.id_offset
            )

    return None

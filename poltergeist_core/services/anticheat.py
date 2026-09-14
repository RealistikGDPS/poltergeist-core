import ipaddress
from dataclasses import dataclass
from datetime import timedelta

from gdformat.enums import Difficulty
from gdformat.enums import Length
from gdformat.requests import Client
from gdformat.requests import LevelScoresRequest

from poltergeist_core.resources import FlagKind
from poltergeist_core.resources import Level
from poltergeist_core.resources import LoginSource
from poltergeist_core.resources import StatCeilings
from poltergeist_core.resources import StatsSnapshot
from poltergeist_core.resources import StatsSource
from poltergeist_core.resources import User
from poltergeist_core.resources import UserBan
from poltergeist_core.resources import UserFlagged
from poltergeist_core.resources import UserStats
from poltergeist_core.services import server_settings
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.utilities import clock
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_ALT_IP_DAYS = 14
_FULL_PERCENT = 100
_MIN_SECONDS = {
    Length.TINY: 0,
    Length.SHORT: 10,
    Length.MEDIUM: 30,
    Length.LONG: 60,
    Length.XL: 120,
    Length.PLATFORMER: 0,
}
_VIA_DEVICE = "device"
_VIA_IP = "ip"


@dataclass(frozen=True, slots=True)
class LinkedAccount:
    user: User
    via: str
    bans: list[UserBan]


def _pack(ip: str) -> bytes | None:
    # The ipaddress module has no non-raising parser.
    try:
        return ipaddress.ip_address(ip).packed
    except ValueError:
        return None


def snapshot(stats: UserStats) -> StatsSnapshot:
    return StatsSnapshot.model_validate(stats.model_dump())


async def ceilings(ctx: AbstractContext) -> StatCeilings:
    """The rated content's totals plus what the official levels award."""

    rated = await ctx.ceilings.load()
    official = await server_settings.current(ctx)

    return StatCeilings(
        stars=rated.stars + official.official_stars,
        moons=rated.moons + official.official_moons,
        demons=rated.demons + official.official_demons,
        secret_coins=rated.secret_coins + official.official_secret_coins,
        user_coins=rated.user_coins,
    )


async def _flag(
    ctx: AbstractContext,
    user: User,
    kind: FlagKind,
    *,
    target_id: int | None,
    evidence: dict[str, object],
    summary: str,
) -> None:
    flag_id = await ctx.flags.create(
        user.id, kind, target_id=target_id, evidence=evidence
    )

    await ctx.events.publish(
        UserFlagged(
            flag_id=flag_id,
            user_id=user.id,
            username=user.username,
            flag_kind=kind,
            summary=summary,
        )
    )

    logger.info(
        "User flagged.",
        extra={"user_id": user.id, "kind": kind.value, "flag_id": flag_id},
    )


async def _linked_user_ids(ctx: AbstractContext, user_id: int) -> dict[int, str]:
    """Other accounts on this user's devices or recent addresses, each with
    the stronger of the two links."""

    since = clock.now() - timedelta(days=_ALT_IP_DAYS)
    linked = dict.fromkeys(
        await ctx.logins.list_user_ids_sharing_ip(user_id, since=since), _VIA_IP
    )

    for other_id in await ctx.devices.list_user_ids_sharing(user_id):
        linked[other_id] = _VIA_DEVICE

    return linked


async def _known_alt_ids(ctx: AbstractContext, user_id: int) -> set[int]:
    known: set[int] = set()

    for flag in await ctx.flags.list_by_user(user_id, FlagKind.ALT_ACCOUNT):
        if flag.evidence is None:
            continue

        for entry in flag.evidence.get("linked", []):
            known.add(int(entry["user_id"]))

    return known


async def _note_alts(ctx: AbstractContext, user: User) -> None:
    linked = await _linked_user_ids(ctx, user.id)

    if not linked:
        return

    new_ids = sorted(set(linked) - await _known_alt_ids(ctx, user.id))

    if not new_ids:
        return

    others = {
        other.id: other for other in await ctx.users.find_many_by_ids(list(linked))
    }
    banned = [other_id for other_id in linked if await ctx.bans.list_active(other_id)]
    names = ", ".join(
        f"{others[other_id].username} ({linked[other_id]})"
        for other_id in new_ids
        if other_id in others
    )

    await _flag(
        ctx,
        user,
        FlagKind.ALT_ACCOUNT,
        target_id=None,
        evidence={
            "linked": [
                {"user_id": other_id, "via": via} for other_id, via in linked.items()
            ],
            "new": new_ids,
            "banned": banned,
        },
        summary=f"Shares a device or address with {names}.",
    )


async def note_login(
    ctx: AbstractContext,
    user: User,
    client: Client,
    *,
    ip: str,
    source: LoginSource,
) -> None:
    packed = _pack(ip)

    if packed is None:
        logger.warning("Login from an unparseable address.", extra={"ip": ip})

        return

    await ctx.logins.create(
        user.id,
        source,
        ip=packed,
        udid=client.udid,
        platform=client.platform,
        game_version=client.game_version,
        binary_version=client.binary_version,
    )

    if client.udid:
        await ctx.devices.upsert(user.id, client.udid, client.platform)

    await _note_alts(ctx, user)


async def note_stats(
    ctx: AbstractContext, user: User, before: UserStats, after: UserStats
) -> None:
    previous = snapshot(before)
    current = snapshot(after)

    if previous == current:
        return

    latest = await ctx.stats_history.latest(user.id)

    if latest is None:
        previous_id = await ctx.stats_history.create(
            user.id, StatsSource.BASELINE, previous
        )
    else:
        previous_id = latest.id

    history_id = await ctx.stats_history.create(user.id, StatsSource.CLIENT, current)
    limits = await ceilings(ctx)

    exceeded = {
        name: {"value": value, "ceiling": ceiling}
        for name, value, ceiling in (
            ("stars", current.stars, limits.stars),
            ("moons", current.moons, limits.moons),
            ("demons", current.demons, limits.demons),
            ("secret_coins", current.secret_coins, limits.secret_coins),
            ("user_coins", current.user_coins, limits.user_coins),
        )
        if value > ceiling
    }

    if not exceeded:
        return

    if await ctx.flags.exists_open(user.id, FlagKind.STATS_CEILING, target_id=None):
        return

    summary = ", ".join(
        f"{name} {detail['value']} over the ceiling of {detail['ceiling']}"
        for name, detail in exceeded.items()
    )

    await _flag(
        ctx,
        user,
        FlagKind.STATS_CEILING,
        target_id=None,
        evidence={
            "exceeded": exceeded,
            "history_id": history_id,
            "previous_history_id": previous_id,
        },
        summary=summary.capitalize() + ".",
    )


async def note_classic_score(
    ctx: AbstractContext,
    user: User,
    level: Level,
    request: LevelScoresRequest,
    *,
    percent: int,
) -> None:
    minimum = _MIN_SECONDS[level.length]
    reasons: list[str] = []

    if percent == _FULL_PERCENT and request.seconds < minimum:
        reasons.append("finished_too_fast")

    if request.clicks == 0 and level.difficulty is not Difficulty.AUTO:
        reasons.append("no_clicks")

    if not reasons:
        return

    if await ctx.flags.exists_open(
        user.id, FlagKind.SCORE_IMPLAUSIBLE, target_id=level.id
    ):
        return

    await _flag(
        ctx,
        user,
        FlagKind.SCORE_IMPLAUSIBLE,
        target_id=level.id,
        evidence={
            "reasons": reasons,
            "level_name": level.name,
            "percent": percent,
            "seconds": request.seconds,
            "minimum_seconds": minimum,
            "clicks": request.clicks,
            "attempts": request.attempts,
        },
        summary=(
            f"{percent}% on {level.name} in {request.seconds}s with "
            f"{request.clicks} clicks ({', '.join(reasons)})."
        ),
    )


async def linked_accounts(ctx: AbstractContext, user_id: int) -> list[LinkedAccount]:
    linked = await _linked_user_ids(ctx, user_id)
    others = await ctx.users.find_many_by_ids(list(linked))

    return [
        LinkedAccount(
            user=other,
            via=linked[other.id],
            bans=await ctx.bans.list_active(other.id),
        )
        for other in sorted(others, key=lambda other: other.id)
    ]

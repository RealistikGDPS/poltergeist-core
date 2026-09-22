from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus

from poltergeist_core.resources import BanType
from poltergeist_core.resources import DemonListPlaced
from poltergeist_core.resources import DemonListPlacement
from poltergeist_core.resources import DemonListRecord
from poltergeist_core.resources import DemonListRecordApproved
from poltergeist_core.resources import DemonListRecordSubmitted
from poltergeist_core.resources import Level
from poltergeist_core.resources import ModTarget
from poltergeist_core.resources import Permission
from poltergeist_core.resources import RecordStatus
from poltergeist_core.resources import ServerSettings
from poltergeist_core.resources import User
from poltergeist_core.services import _audit
from poltergeist_core.services import server_settings
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.services._common import is_error
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

FULL_PERCENT = 100
_URL_MAX = 255
_NOTES_MAX = 500
_REVIEW_NOTE_MAX = 255
_DAILY_WINDOW = 86_400


class DemonListError(ServiceError, StrEnum):
    NOT_PERMITTED = "not_permitted"
    NOT_FOUND = "not_found"
    LEVEL_NOT_FOUND = "level_not_found"
    USER_NOT_FOUND = "user_not_found"
    ALREADY_LISTED = "already_listed"
    INVALID_POSITION = "invalid_position"
    INVALID_REQUIREMENT = "invalid_requirement"
    INVALID_PERCENT = "invalid_percent"
    BELOW_REQUIREMENT = "below_requirement"
    INVALID_URL = "invalid_url"
    INVALID_NOTES = "invalid_notes"
    SUBMISSIONS_CLOSED = "submissions_closed"
    BANNED = "banned"
    ALREADY_PENDING = "already_pending"
    NOT_IMPROVED = "not_improved"
    ALREADY_REVIEWED = "already_reviewed"
    RATE_LIMITED = "rate_limited"

    def service(self) -> str:
        return "demon_list"

    def status_code(self) -> int:
        match self:
            case (
                DemonListError.NOT_PERMITTED
                | DemonListError.SUBMISSIONS_CLOSED
                | DemonListError.BANNED
            ):
                return HTTPStatus.FORBIDDEN
            case (
                DemonListError.NOT_FOUND
                | DemonListError.LEVEL_NOT_FOUND
                | DemonListError.USER_NOT_FOUND
            ):
                return HTTPStatus.NOT_FOUND
            case (
                DemonListError.ALREADY_LISTED
                | DemonListError.ALREADY_PENDING
                | DemonListError.NOT_IMPROVED
                | DemonListError.ALREADY_REVIEWED
            ):
                return HTTPStatus.CONFLICT
            case (
                DemonListError.INVALID_POSITION
                | DemonListError.INVALID_REQUIREMENT
                | DemonListError.INVALID_PERCENT
                | DemonListError.BELOW_REQUIREMENT
                | DemonListError.INVALID_URL
                | DemonListError.INVALID_NOTES
            ):
                return HTTPStatus.BAD_REQUEST
            case DemonListError.RATE_LIMITED:
                return HTTPStatus.TOO_MANY_REQUESTS


@dataclass(frozen=True, slots=True)
class ListedLevel:
    placement: DemonListPlacement
    level: Level
    creator: User | None
    points: int
    record_count: int


@dataclass(frozen=True, slots=True)
class DemonList:
    entries: list[ListedLevel]


@dataclass(frozen=True, slots=True)
class RecordEntry:
    record: DemonListRecord
    user: User | None
    points: int


@dataclass(frozen=True, slots=True)
class PlacementDetail:
    placement: DemonListPlacement
    level: Level
    creator: User | None
    points: int
    records: list[RecordEntry]


@dataclass(frozen=True, slots=True)
class Score:
    user_id: int
    points: int
    records: int


@dataclass(frozen=True, slots=True)
class Standing:
    rank: int
    user: User
    points: int
    records: int


@dataclass(frozen=True, slots=True)
class Standings:
    entries: list[Standing]
    page: int
    size: int
    total: int


@dataclass(frozen=True, slots=True)
class PlayerRecord:
    record: DemonListRecord
    placement: DemonListPlacement
    level: Level | None
    points: int


@dataclass(frozen=True, slots=True)
class PlayerSummary:
    points: int
    rank: int | None
    records: list[PlayerRecord]


def placement_points(position: int, site: ServerSettings) -> int:
    decay = site.demon_list_decay_percent / 100

    return round(site.demon_list_top_points * decay ** (position - 1))


def record_points(full: int, percent: int, requirement: int) -> int:
    """A completion earns the placement's points; a record between the
    requirement and a completion earns a proportional share."""

    if percent >= FULL_PERCENT:
        return full

    if requirement >= FULL_PERCENT or percent < requirement:
        return 0

    return round(full * (percent - requirement) / (FULL_PERCENT - requirement))


def _valid_url(url: str) -> bool:
    if url == "":
        return True

    if len(url) > _URL_MAX:
        return False

    return url.startswith(("https://", "http://"))


def _valid_requirement(requirement: int) -> bool:
    return 1 <= requirement <= FULL_PERCENT


def _valid_percent(percent: int) -> bool:
    return 1 <= percent <= FULL_PERCENT


async def _require(
    ctx: AbstractContext, actor_user_id: int, permission: Permission
) -> DemonListError | None:
    if await ctx.permissions.has(actor_user_id, permission):
        return None

    return DemonListError.NOT_PERMITTED


async def _live_placement(
    ctx: AbstractContext, placement_id: int
) -> DemonListError.OnSuccess[DemonListPlacement]:
    placement = await ctx.demon_list_placements.find_by_id(placement_id)

    if placement is None:
        return DemonListError.NOT_FOUND

    return placement


async def _scores(ctx: AbstractContext, site: ServerSettings) -> list[Score]:
    """Every scoring player, best first; ties keep the lower user id first."""

    ranked = await ctx.demon_list_records.list_ranked()
    points: dict[int, int] = {}
    counts: dict[int, int] = {}

    for entry in ranked:
        full = placement_points(entry.position, site)
        points[entry.user_id] = points.get(entry.user_id, 0) + record_points(
            full, entry.percent, entry.requirement
        )
        counts[entry.user_id] = counts.get(entry.user_id, 0) + 1

    scores = [
        Score(user_id=user_id, points=total, records=counts[user_id])
        for user_id, total in points.items()
        if total > 0
    ]

    return sorted(scores, key=lambda score: (-score.points, score.user_id))


def _ranks(scores: list[Score]) -> dict[int, int]:
    """Competition ranking: equal points share a rank and the next rank skips."""

    ranks: dict[int, int] = {}
    rank = 0

    for index, score in enumerate(scores):
        if index == 0 or score.points != scores[index - 1].points:
            rank = index + 1

        ranks[score.user_id] = rank

    return ranks


async def overview(ctx: AbstractContext) -> DemonList:
    """The list in order; a placement whose level has since been deleted is
    left out here and shown to the staff instead."""

    site = await server_settings.current(ctx)
    placements = await ctx.demon_list_placements.list_all()
    levels = {
        level.id: level
        for level in await ctx.levels.find_many_by_ids(
            [placement.level_id for placement in placements]
        )
    }
    creators = {
        user.id: user
        for user in await ctx.users.find_many_by_ids(
            list({level.user_id for level in levels.values()})
        )
    }
    counts = await ctx.demon_list_records.count_by_status(
        [placement.id for placement in placements]
    )
    entries: list[ListedLevel] = []

    for placement in placements:
        level = levels.get(placement.level_id)

        if level is None:
            continue

        entries.append(
            ListedLevel(
                placement=placement,
                level=level,
                creator=creators.get(level.user_id),
                points=placement_points(placement.position, site),
                record_count=counts.get(placement.id, {}).get(
                    RecordStatus.APPROVED, 0
                ),
            )
        )

    return DemonList(entries=entries)


async def placement_detail(
    ctx: AbstractContext, *, level_id: int
) -> DemonListError.OnSuccess[PlacementDetail]:
    placement = await ctx.demon_list_placements.find_by_level(level_id)

    if placement is None:
        return DemonListError.NOT_FOUND

    level = await ctx.levels.find_by_id(level_id)

    if level is None:
        return DemonListError.NOT_FOUND

    site = await server_settings.current(ctx)
    full = placement_points(placement.position, site)
    records = await ctx.demon_list_records.list_approved_by_placement(placement.id)
    user_ids = [record.user_id for record in records]
    user_ids.append(level.user_id)
    users = {user.id: user for user in await ctx.users.find_many_by_ids(user_ids)}

    return PlacementDetail(
        placement=placement,
        level=level,
        creator=users.get(level.user_id),
        points=full,
        records=[
            RecordEntry(
                record=record,
                user=users.get(record.user_id),
                points=record_points(full, record.percent, placement.requirement),
            )
            for record in records
        ],
    )


async def standings(ctx: AbstractContext, *, page: int, size: int) -> Standings:
    site = await server_settings.current(ctx)
    scores = await _scores(ctx, site)
    ranks = _ranks(scores)
    start = max(page, 0) * size
    shown = scores[start : start + size]
    users = {
        user.id: user
        for user in await ctx.users.find_many_by_ids(
            [score.user_id for score in shown]
        )
    }

    return Standings(
        entries=[
            Standing(
                rank=ranks[score.user_id],
                user=users[score.user_id],
                points=score.points,
                records=score.records,
            )
            for score in shown
            if score.user_id in users
        ],
        page=max(page, 0),
        size=size,
        total=len(scores),
    )


async def player_summary(ctx: AbstractContext, *, user_id: int) -> PlayerSummary:
    site = await server_settings.current(ctx)
    scores = await _scores(ctx, site)
    records = await ctx.demon_list_records.list_approved_by_user(user_id)
    placements = {
        placement.id: placement
        for placement in await ctx.demon_list_placements.find_many_by_ids(
            [record.placement_id for record in records]
        )
    }
    levels = {
        level.id: level
        for level in await ctx.levels.find_many_by_ids(
            [placement.level_id for placement in placements.values()]
        )
    }
    entries: list[PlayerRecord] = []

    for record in records:
        placement = placements.get(record.placement_id)

        if placement is None:
            continue

        live = placement.deleted_at is None
        full = placement_points(placement.position, site) if live else 0

        entries.append(
            PlayerRecord(
                record=record,
                placement=placement,
                level=levels.get(placement.level_id),
                points=record_points(full, record.percent, placement.requirement),
            )
        )

    entries.sort(key=lambda entry: (-entry.points, entry.placement.position))
    own = next((score for score in scores if score.user_id == user_id), None)

    return PlayerSummary(
        points=0 if own is None else own.points,
        rank=None if own is None else _ranks(scores)[user_id],
        records=entries,
    )


async def place(
    ctx: AbstractContext,
    *,
    actor_user_id: int,
    level_id: int,
    position: int | None,
    requirement: int,
    video_url: str,
) -> DemonListError.OnSuccess[int]:
    """Adds a level at `position` (the bottom when None), pushing the levels
    from there down by one."""

    refused = await _require(ctx, actor_user_id, Permission.DEMON_LIST_MANAGE)

    if refused is not None:
        return refused

    level = await ctx.levels.find_by_id(level_id)

    if level is None:
        return DemonListError.LEVEL_NOT_FOUND

    if not _valid_requirement(requirement):
        return DemonListError.INVALID_REQUIREMENT

    if not _valid_url(video_url):
        return DemonListError.INVALID_URL

    existing = await ctx.demon_list_placements.find_by_level(
        level_id, include_removed=True
    )

    if existing is not None and existing.deleted_at is None:
        return DemonListError.ALREADY_LISTED

    count = await ctx.demon_list_placements.count()
    target = count + 1 if position is None else position

    if not 1 <= target <= count + 1:
        return DemonListError.INVALID_POSITION

    await ctx.demon_list_placements.shift(low=target, high=count, delta=1)

    if existing is None:
        placement_id = await ctx.demon_list_placements.create(
            level_id,
            position=target,
            requirement=requirement,
            video_url=video_url,
            added_by_user_id=actor_user_id,
        )
    else:
        placement_id = existing.id

        await ctx.demon_list_placements.revive(
            placement_id,
            position=target,
            requirement=requirement,
            video_url=video_url,
            added_by_user_id=actor_user_id,
        )

    await _audit.record(
        ctx,
        actor_user_id,
        "place",
        ModTarget.DEMON_LIST_PLACEMENT,
        placement_id,
        {"level_id": level_id, "position": target, "requirement": requirement},
    )

    await ctx.events.publish(
        DemonListPlaced(
            placement_id=placement_id,
            level_id=level_id,
            level_name=level.name,
            position=target,
            actor_user_id=actor_user_id,
        )
    )

    return placement_id


async def move(
    ctx: AbstractContext, *, actor_user_id: int, placement_id: int, position: int
) -> DemonListError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.DEMON_LIST_MANAGE)

    if refused is not None:
        return refused

    placement = await _live_placement(ctx, placement_id)

    if is_error(placement):
        return placement

    count = await ctx.demon_list_placements.count()

    if not 1 <= position <= count:
        return DemonListError.INVALID_POSITION

    if position == placement.position:
        return None

    if position < placement.position:
        await ctx.demon_list_placements.shift(
            low=position, high=placement.position - 1, delta=1
        )
    else:
        await ctx.demon_list_placements.shift(
            low=placement.position + 1, high=position, delta=-1
        )

    await ctx.demon_list_placements.set_position(placement.id, position)

    await _audit.record(
        ctx,
        actor_user_id,
        "move",
        ModTarget.DEMON_LIST_PLACEMENT,
        placement.id,
        {"level_id": placement.level_id, "from": placement.position, "to": position},
    )

    level = await ctx.levels.find_by_id(placement.level_id)

    await ctx.events.publish(
        DemonListPlaced(
            placement_id=placement.id,
            level_id=placement.level_id,
            level_name="" if level is None else level.name,
            position=position,
            actor_user_id=actor_user_id,
        )
    )

    return None


async def update_placement(
    ctx: AbstractContext,
    *,
    actor_user_id: int,
    placement_id: int,
    requirement: int,
    video_url: str,
) -> DemonListError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.DEMON_LIST_MANAGE)

    if refused is not None:
        return refused

    placement = await _live_placement(ctx, placement_id)

    if is_error(placement):
        return placement

    if not _valid_requirement(requirement):
        return DemonListError.INVALID_REQUIREMENT

    if not _valid_url(video_url):
        return DemonListError.INVALID_URL

    await ctx.demon_list_placements.update(
        placement.id, requirement=requirement, video_url=video_url
    )

    await _audit.record(
        ctx,
        actor_user_id,
        "update",
        ModTarget.DEMON_LIST_PLACEMENT,
        placement.id,
        {"level_id": placement.level_id, "requirement": requirement},
    )

    return None


async def remove(
    ctx: AbstractContext, *, actor_user_id: int, placement_id: int
) -> DemonListError.OnSuccess[None]:
    """Takes a level off the list; its records stay and count again should
    the level be placed once more."""

    refused = await _require(ctx, actor_user_id, Permission.DEMON_LIST_MANAGE)

    if refused is not None:
        return refused

    placement = await _live_placement(ctx, placement_id)

    if is_error(placement):
        return placement

    count = await ctx.demon_list_placements.count()
    await ctx.demon_list_placements.soft_delete(placement.id)
    await ctx.demon_list_placements.shift(
        low=placement.position + 1, high=count, delta=-1
    )

    await _audit.record(
        ctx,
        actor_user_id,
        "remove",
        ModTarget.DEMON_LIST_PLACEMENT,
        placement.id,
        {"level_id": placement.level_id, "position": placement.position},
    )

    return None


def _validate_record(
    placement: DemonListPlacement, *, percent: int, urls: tuple[str, ...], notes: str
) -> DemonListError | None:
    if not _valid_percent(percent):
        return DemonListError.INVALID_PERCENT

    if percent < placement.requirement:
        return DemonListError.BELOW_REQUIREMENT

    if not all(_valid_url(url) for url in urls):
        return DemonListError.INVALID_URL

    if len(notes) > _NOTES_MAX:
        return DemonListError.INVALID_NOTES

    return None


async def submit(
    ctx: AbstractContext,
    *,
    user_id: int,
    placement_id: int,
    percent: int,
    video_url: str,
    raw_footage_url: str,
    notes: str,
) -> DemonListError.OnSuccess[int]:
    site = await server_settings.current(ctx)

    if not site.demon_list_submissions_enabled:
        return DemonListError.SUBMISSIONS_CLOSED

    refused = await _require(ctx, user_id, Permission.DEMON_LIST_SUBMIT)

    if refused is not None:
        return refused

    if await ctx.bans.find_active(user_id, BanType.DEMON_LIST) is not None:
        return DemonListError.BANNED

    placement = await _live_placement(ctx, placement_id)

    if is_error(placement):
        return placement

    invalid = _validate_record(
        placement, percent=percent, urls=(video_url, raw_footage_url), notes=notes
    )

    if invalid is not None:
        return invalid

    if await ctx.demon_list_records.find_pending(placement.id, user_id) is not None:
        return DemonListError.ALREADY_PENDING

    approved = await ctx.demon_list_records.find_approved(placement.id, user_id)

    if approved is not None and approved.percent >= percent:
        return DemonListError.NOT_IMPROVED

    within_allowance = await ctx.rate_limits.hit(
        "demon_list_submit",
        str(user_id),
        limit=site.demon_list_daily_submissions,
        window_seconds=_DAILY_WINDOW,
    )

    if not within_allowance:
        return DemonListError.RATE_LIMITED

    record_id = await ctx.demon_list_records.create(
        placement.id,
        user_id,
        percent=percent,
        status=RecordStatus.PENDING,
        video_url=video_url,
        raw_footage_url=raw_footage_url,
        notes=notes,
        reviewed_by_user_id=None,
    )
    user = await ctx.users.find_by_id(user_id)
    level = await ctx.levels.find_by_id(placement.level_id)

    await ctx.events.publish(
        DemonListRecordSubmitted(
            record_id=record_id,
            level_id=placement.level_id,
            level_name="" if level is None else level.name,
            user_id=user_id,
            username="" if user is None else user.username,
            percent=percent,
            video_url=video_url,
        )
    )

    logger.info(
        "Demon list record submitted.",
        extra={"record_id": record_id, "user_id": user_id, "percent": percent},
    )

    return record_id


async def withdraw(
    ctx: AbstractContext, *, user_id: int, record_id: int
) -> DemonListError.OnSuccess[None]:
    record = await ctx.demon_list_records.find_by_id(record_id)

    if record is None or record.user_id != user_id:
        return DemonListError.NOT_FOUND

    if record.status is not RecordStatus.PENDING:
        return DemonListError.ALREADY_REVIEWED

    await ctx.demon_list_records.soft_delete(record.id)

    return None


async def _pending_record(
    ctx: AbstractContext, actor_user_id: int, record_id: int
) -> DemonListError.OnSuccess[DemonListRecord]:
    refused = await _require(ctx, actor_user_id, Permission.DEMON_LIST_REVIEW)

    if refused is not None:
        return refused

    record = await ctx.demon_list_records.find_by_id(record_id)

    if record is None:
        return DemonListError.NOT_FOUND

    if record.status is not RecordStatus.PENDING:
        return DemonListError.ALREADY_REVIEWED

    return record


async def _announce_approval(
    ctx: AbstractContext,
    record: DemonListRecord,
    *,
    placement: DemonListPlacement | None,
    actor_user_id: int,
) -> None:
    site = await server_settings.current(ctx)
    user = await ctx.users.find_by_id(record.user_id)
    level = None
    points = 0

    if placement is not None:
        level = await ctx.levels.find_by_id(placement.level_id)
        points = record_points(
            placement_points(placement.position, site),
            record.percent,
            placement.requirement,
        )

    await ctx.events.publish(
        DemonListRecordApproved(
            record_id=record.id,
            level_id=0 if placement is None else placement.level_id,
            level_name="" if level is None else level.name,
            user_id=record.user_id,
            username="" if user is None else user.username,
            percent=record.percent,
            points=points,
            actor_user_id=actor_user_id,
        )
    )


async def approve(
    ctx: AbstractContext, *, actor_user_id: int, record_id: int
) -> DemonListError.OnSuccess[None]:
    """The newest approval is the player's record on that level; any earlier
    approved one is superseded whatever its percent."""

    record = await _pending_record(ctx, actor_user_id, record_id)

    if is_error(record):
        return record

    await ctx.demon_list_records.review(
        record.id,
        RecordStatus.APPROVED,
        reviewed_by_user_id=actor_user_id,
        review_note="",
    )
    await ctx.demon_list_records.supersede(
        record.placement_id, record.user_id, except_record_id=record.id
    )

    await _audit.record(
        ctx,
        actor_user_id,
        "approve",
        ModTarget.DEMON_LIST_RECORD,
        record.id,
        {
            "user_id": record.user_id,
            "placement_id": record.placement_id,
            "percent": record.percent,
        },
    )

    placement = await ctx.demon_list_placements.find_by_id(record.placement_id)
    await _announce_approval(
        ctx, record, placement=placement, actor_user_id=actor_user_id
    )

    return None


async def reject(
    ctx: AbstractContext, *, actor_user_id: int, record_id: int, note: str
) -> DemonListError.OnSuccess[None]:
    record = await _pending_record(ctx, actor_user_id, record_id)

    if is_error(record):
        return record

    if len(note) > _REVIEW_NOTE_MAX:
        return DemonListError.INVALID_NOTES

    await ctx.demon_list_records.review(
        record.id,
        RecordStatus.REJECTED,
        reviewed_by_user_id=actor_user_id,
        review_note=note,
    )

    await _audit.record(
        ctx,
        actor_user_id,
        "reject",
        ModTarget.DEMON_LIST_RECORD,
        record.id,
        {
            "user_id": record.user_id,
            "placement_id": record.placement_id,
            "percent": record.percent,
            "note": note,
        },
    )

    return None


async def delete_record(
    ctx: AbstractContext, *, actor_user_id: int, record_id: int
) -> DemonListError.OnSuccess[None]:
    refused = await _require(ctx, actor_user_id, Permission.DEMON_LIST_REVIEW)

    if refused is not None:
        return refused

    record = await ctx.demon_list_records.find_by_id(record_id)

    if record is None:
        return DemonListError.NOT_FOUND

    await ctx.demon_list_records.soft_delete(record.id)

    await _audit.record(
        ctx,
        actor_user_id,
        "delete",
        ModTarget.DEMON_LIST_RECORD,
        record.id,
        {
            "user_id": record.user_id,
            "placement_id": record.placement_id,
            "percent": record.percent,
            "status": record.status.value,
        },
    )

    return None


async def add_record(
    ctx: AbstractContext,
    *,
    actor_user_id: int,
    placement_id: int,
    user_id: int,
    percent: int,
    video_url: str,
) -> DemonListError.OnSuccess[int]:
    """A record entered by a reviewer is approved on the spot."""

    refused = await _require(ctx, actor_user_id, Permission.DEMON_LIST_REVIEW)

    if refused is not None:
        return refused

    placement = await _live_placement(ctx, placement_id)

    if is_error(placement):
        return placement

    if await ctx.users.find_by_id(user_id) is None:
        return DemonListError.USER_NOT_FOUND

    invalid = _validate_record(placement, percent=percent, urls=(video_url,), notes="")

    if invalid is not None:
        return invalid

    record_id = await ctx.demon_list_records.create(
        placement.id,
        user_id,
        percent=percent,
        status=RecordStatus.APPROVED,
        video_url=video_url,
        raw_footage_url="",
        notes="",
        reviewed_by_user_id=actor_user_id,
    )
    await ctx.demon_list_records.supersede(
        placement.id, user_id, except_record_id=record_id
    )

    await _audit.record(
        ctx,
        actor_user_id,
        "add",
        ModTarget.DEMON_LIST_RECORD,
        record_id,
        {"user_id": user_id, "placement_id": placement.id, "percent": percent},
    )

    record = await ctx.demon_list_records.find_by_id(record_id)

    if record is not None:
        await _announce_approval(
            ctx, record, placement=placement, actor_user_id=actor_user_id
        )

    return record_id

from enum import StrEnum
from http import HTTPStatus

from gdformat import crypto
from gdformat import objects
from gdformat.enums import LevelScoreType
from gdformat.enums import PlatformerMode
from gdformat.requests import LevelScoresRequest

from poltergeist_core.resources import BanType
from poltergeist_core.resources import LevelScore
from poltergeist_core.resources import Permission
from poltergeist_core.resources import PlatformerScore
from poltergeist_core.services import _wire
from poltergeist_core.services import timely
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.services.auth import Session
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_TOP_LIMIT = 100
_MAX_PERCENT = 100


class ScoreError(ServiceError, StrEnum):
    NOT_FOUND = "not_found"
    INVALID = "invalid"

    def service(self) -> str:
        return "scores"

    def status_code(self) -> int:
        match self:
            case ScoreError.NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case ScoreError.INVALID:
                return HTTPStatus.BAD_REQUEST


async def _may_submit(ctx: AbstractContext, user_id: int) -> bool:
    if not await ctx.permissions.has(user_id, Permission.SCORES_SUBMIT):
        return False

    return await ctx.bans.find_active(user_id, BanType.LEADERBOARD) is None


async def _submit_classic(
    ctx: AbstractContext,
    session: Session,
    request: LevelScoresRequest,
    timely_level_id: int | None,
    max_coins: int,
) -> None:
    percent = request.percent

    if percent is None or not 0 < percent <= _MAX_PERCENT:
        return

    expected = crypto.classic_leaderboard_seed(request.clicks, percent, request.seconds)

    if request.seed != expected:
        logger.warning(
            "Rejected a classic score with a bad seed.",
            extra={"user_id": session.user.id, "level_id": request.level_id},
        )

        return

    if not await _may_submit(ctx, session.user.id):
        return

    existing = await ctx.level_scores.find(
        request.level_id, session.user.id, timely_level_id
    )

    if existing is not None and existing.percent > percent:
        return

    await ctx.level_scores.upsert(
        level_id=request.level_id,
        user_id=session.user.id,
        timely_level_id=timely_level_id,
        percent=percent,
        attempts=max(request.attempts, 0),
        clicks=max(request.clicks, 0),
        seconds=max(request.seconds, 0),
        coins=min(max(request.coins, 0), max_coins),
        progress=list(request.progress),
        level_version=request.level_version,
    )


async def _submit_platformer(
    ctx: AbstractContext,
    session: Session,
    request: LevelScoresRequest,
    timely_level_id: int | None,
    max_coins: int,
) -> None:
    if request.time_ms <= 0 and request.points <= 0:
        return

    if not await _may_submit(ctx, session.user.id):
        return

    existing = await ctx.platformer_scores.find(
        request.level_id, session.user.id, timely_level_id
    )
    time_ms = max(request.time_ms, 0)
    points = max(request.points, 0)

    if existing is not None:
        better_time = time_ms > 0 and (
            existing.time_ms == 0 or time_ms < existing.time_ms
        )
        better_points = points > existing.points

        if not better_time and not better_points:
            return

        if not better_time:
            time_ms = existing.time_ms

        if not better_points:
            points = existing.points

    await ctx.platformer_scores.upsert(
        level_id=request.level_id,
        user_id=session.user.id,
        timely_level_id=timely_level_id,
        time_ms=time_ms,
        points=points,
        attempts=max(request.attempts, 0),
        clicks=max(request.clicks, 0),
        coins=min(max(request.coins, 0), max_coins),
        level_version=request.level_version,
    )


async def _rows(
    ctx: AbstractContext,
    entries: list[tuple[int, int, int, str]],
) -> list[objects.LevelScore]:
    """`entries` are `(user_id, value, coins, age)` in leaderboard order."""

    user_ids = [entry[0] for entry in entries]
    users = {user.id: user for user in await ctx.users.find_many_by_ids(user_ids)}
    stats = {
        entry.user_id: entry
        for entry in await ctx.stats.find_many_by_user_ids(user_ids)
    }
    scores: list[objects.LevelScore] = []

    for user_id, value, coins, age in entries:
        if user_id not in users or user_id not in stats:
            continue

        scores.append(
            objects.LevelScore(
                player=_wire.player(users[user_id], stats[user_id]),
                value=value,
                rank=len(scores) + 1,
                age=age,
                coins=coins,
            )
        )

    return scores


async def _classic_board(
    ctx: AbstractContext,
    session: Session,
    request: LevelScoresRequest,
    timely_level_id: int | None,
) -> list[LevelScore]:
    match request.score_type:
        case LevelScoreType.TOP:
            return await ctx.level_scores.list_top(
                request.level_id, timely_level_id, _TOP_LIMIT
            )
        case LevelScoreType.WEEK:
            return await ctx.level_scores.list_week(request.level_id, _TOP_LIMIT)
        case LevelScoreType.FRIENDS:
            friends = await ctx.friendships.list_friend_ids(session.user.id)

            return await ctx.level_scores.list_friends(
                request.level_id, timely_level_id, [session.user.id, *friends]
            )


async def _platformer_board(
    ctx: AbstractContext,
    session: Session,
    request: LevelScoresRequest,
    timely_level_id: int | None,
) -> list[PlatformerScore]:
    match request.score_type:
        case LevelScoreType.TOP:
            return await ctx.platformer_scores.list_top(
                request.level_id, timely_level_id, request.mode, _TOP_LIMIT
            )
        case LevelScoreType.WEEK:
            return await ctx.platformer_scores.list_week(
                request.level_id, request.mode, _TOP_LIMIT
            )
        case LevelScoreType.FRIENDS:
            friends = await ctx.friendships.list_friend_ids(session.user.id)

            return await ctx.platformer_scores.list_friends(
                request.level_id,
                timely_level_id,
                request.mode,
                [session.user.id, *friends],
            )


def _platformer_value(score: PlatformerScore, mode: PlatformerMode) -> int:
    match mode:
        case PlatformerMode.TIME:
            return score.time_ms
        case PlatformerMode.POINTS:
            return score.points


async def level_scores(
    ctx: AbstractContext, session: Session, request: LevelScoresRequest
) -> ScoreError.OnSuccess[list[objects.LevelScore]]:
    """Submits the player's result when one is carried, then lists the board."""

    level = await ctx.levels.find_by_id(request.level_id)

    if level is None:
        return ScoreError.NOT_FOUND

    timely_level_id = None

    if request.timely_id:
        entry = await timely.resolve_wire_id(ctx, request.timely_id)

        if entry is not None and entry.level_id == level.id:
            timely_level_id = entry.id

    if request.platformer:
        await _submit_platformer(ctx, session, request, timely_level_id, level.coins)
        platformer_rows = await _platformer_board(
            ctx, session, request, timely_level_id
        )

        return await _rows(
            ctx,
            [
                (
                    score.user_id,
                    _platformer_value(score, request.mode),
                    score.coins,
                    _wire.age(score.submitted_at),
                )
                for score in platformer_rows
            ],
        )

    await _submit_classic(ctx, session, request, timely_level_id, level.coins)
    classic_rows = await _classic_board(ctx, session, request, timely_level_id)

    return await _rows(
        ctx,
        [
            (score.user_id, score.percent, score.coins, _wire.age(score.submitted_at))
            for score in classic_rows
        ],
    )

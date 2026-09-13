from datetime import timedelta

from poltergeist_core.resources import DailyCount
from poltergeist_core.resources import Dashboard
from poltergeist_core.resources import RecentAction
from poltergeist_core.resources import Snapshot
from poltergeist_core.resources import Trend
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.utilities import clock

_SNAPSHOT_SECONDS = 300
_SERIES_DAYS = 30
_TOP_CREATORS = 10
_DASHBOARD_DAYS_MAX = 365
_RECENT_ACTIONS = 12


async def snapshot(ctx: AbstractContext) -> Snapshot:
    """The public statistics, rebuilt at most every few minutes. A stampede
    on a server this size costs a handful of duplicate aggregates, so there
    is no lock."""

    cached = await ctx.analytics.find_snapshot()

    if cached is not None:
        return cached

    built = Snapshot(
        totals=await ctx.analytics.totals(),
        registrations=await ctx.analytics.registrations_per_day(_SERIES_DAYS),
        uploads=await ctx.analytics.uploads_per_day(_SERIES_DAYS),
        active_users=await ctx.analytics.active_users_per_day(_SERIES_DAYS),
        difficulties=await ctx.analytics.difficulty_distribution(),
        top_creators=await ctx.analytics.top_creators(_TOP_CREATORS),
        generated_at=clock.now(),
    )
    await ctx.analytics.store_snapshot(built, seconds=_SNAPSHOT_SECONDS)

    return built


def _trend(series: list[DailyCount], days: int) -> Trend:
    """Splits a double-length series into the window and the one before it."""

    boundary = clock.now().date() - timedelta(days=days)
    current = [row for row in series if row.day > boundary]
    by_day = {row.day: row.count for row in current}

    return Trend(
        current=current,
        current_total=sum(row.count for row in current),
        previous_total=sum(row.count for row in series if row.day <= boundary),
        sparkline=[
            by_day.get(boundary + timedelta(days=offset), 0)
            for offset in range(1, days + 1)
        ],
    )


async def _recent_actions(ctx: AbstractContext) -> list[RecentAction]:
    actions = await ctx.mod_actions.list_recent(
        user_id=None,
        target_type=None,
        target_id=None,
        page=0,
        size=_RECENT_ACTIONS,
    )
    actors = await ctx.users.find_many_by_ids([action.user_id for action in actions])
    names = {actor.id: actor.username for actor in actors}

    return [
        RecentAction(action=action, actor_username=names.get(action.user_id, ""))
        for action in actions
    ]


async def dashboard(ctx: AbstractContext, *, days: int) -> Dashboard:
    """The admin overview over the last `days`, with every trend compared
    against the window before it. Not cached: only operators load it."""

    days = min(max(days, 1), _DASHBOARD_DAYS_MAX)
    span = days * 2

    return Dashboard(
        days=days,
        totals=await ctx.analytics.totals(),
        registrations=_trend(await ctx.analytics.registrations_per_day(span), days),
        active_users=_trend(await ctx.analytics.active_users_per_day(span), days),
        uploads=_trend(await ctx.analytics.uploads_per_day(span), days),
        comments=_trend(await ctx.analytics.comments_per_day(span), days),
        mod_actions=_trend(await ctx.analytics.mod_actions_per_day(span), days),
        comment_hours=await ctx.analytics.comments_per_hour(days),
        difficulties=await ctx.analytics.difficulty_distribution(),
        stars=await ctx.analytics.stars_distribution(),
        lengths=await ctx.analytics.length_distribution(),
        top_creators=await ctx.analytics.top_creators(_TOP_CREATORS),
        recent_actions=await _recent_actions(ctx),
        generated_at=clock.now(),
    )

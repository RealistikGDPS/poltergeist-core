from poltergeist_core.resources import Snapshot
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.utilities import clock

_SNAPSHOT_SECONDS = 300
_SERIES_DAYS = 30
_TOP_CREATORS = 10


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

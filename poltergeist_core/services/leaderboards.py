from poltergeist_core.resources import BanType
from poltergeist_core.resources import LeaderboardKind
from poltergeist_core.resources import LeaderboardsRebuilt
from poltergeist_core.resources import Permission
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_BATCH = 1000


async def rebuild(ctx: AbstractContext) -> int:
    """Rebuilds every Redis ranking from MySQL. Safe to run while serving."""

    for kind in LeaderboardKind:
        await ctx.leaderboards.clear(kind)

    leaderboard_banned = set(await ctx.bans.list_banned_user_ids(BanType.LEADERBOARD))
    creator_banned = set(await ctx.bans.list_banned_user_ids(BanType.CREATOR))
    non_players = set(await ctx.users.list_non_player_ids())
    last_id = 0
    total = 0

    while True:
        batch = await ctx.stats.list_ranked_after(last_id, _BATCH)

        if not batch:
            break

        for entry in batch:
            last_id = entry.user_id

            if entry.user_id in leaderboard_banned or entry.user_id in non_players:
                continue

            if not await ctx.permissions.has(
                entry.user_id, Permission.LEADERBOARD_RANK
            ):
                continue

            await ctx.leaderboards.set_scores(
                entry.user_id,
                {
                    LeaderboardKind.STARS: entry.stars,
                    LeaderboardKind.MOONS: entry.moons,
                    LeaderboardKind.DEMONS: entry.demons,
                    LeaderboardKind.USER_COINS: entry.user_coins,
                    LeaderboardKind.CREATOR_POINTS: (
                        0 if entry.user_id in creator_banned else entry.creator_points
                    ),
                },
            )
            total += 1

    await ctx.events.publish(LeaderboardsRebuilt(users=total))

    logger.info("Rebuilt the leaderboards.", extra={"users": total})

    return total

import random
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus

from gdformat import objects
from gdformat.enums import ChestType
from gdformat.enums import QuestItem
from gdformat.enums import RewardType
from gdformat.enums import Shard

from poltergeist_core import settings
from poltergeist_core.resources import ChestClaim
from poltergeist_core.resources import Permission
from poltergeist_core.resources import Quest
from poltergeist_core.resources import SecretReward
from poltergeist_core.services import _wire
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.services._common import is_error
from poltergeist_core.services.auth import Session
from poltergeist_core.utilities import clock
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_SMALL_ORBS = range(20, 65, 5)
_SMALL_DIAMONDS = range(1, 4)
_SMALL_SHARD_CHANCE = 0.2
_LARGE_ORBS = range(200, 425, 25)
_LARGE_DIAMONDS = range(4, 11)
_LARGE_SHARD_CHANCE = 0.6
_LARGE_KEY_CHANCE = 0.25
_SHARDS = (Shard.FIRE, Shard.ICE, Shard.POISON, Shard.SHADOW, Shard.LAVA)
_QUEST_SLOTS = (QuestItem.ORBS, QuestItem.COINS, QuestItem.STARS)


class RewardError(ServiceError, StrEnum):
    NOT_PERMITTED = "not_permitted"
    NOT_READY = "not_ready"
    NOT_FOUND = "not_found"
    ALREADY_CLAIMED = "already_claimed"
    EXHAUSTED = "exhausted"
    UNAVAILABLE = "unavailable"

    def service(self) -> str:
        return "rewards"

    def status_code(self) -> int:
        match self:
            case RewardError.NOT_PERMITTED:
                return HTTPStatus.FORBIDDEN
            case RewardError.NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case _:
                return HTTPStatus.CONFLICT


@dataclass(frozen=True, slots=True)
class ChestState:
    seconds_left: int
    contents: objects.Chest | None
    count: int


@dataclass(frozen=True, slots=True)
class RewardsPayload:
    small: ChestState
    large: ChestState
    reward_type: RewardType


@dataclass(frozen=True, slots=True)
class ChallengesPayload:
    quests: tuple[objects.Quest, objects.Quest, objects.Quest]
    seconds_left: int


@dataclass(frozen=True, slots=True)
class SecretRewardPayload:
    reward_id: int
    chest_type: ChestType
    rewards: tuple[objects.RewardStack, ...]


def _cooldown(chest_type: ChestType) -> int:
    match chest_type:
        case ChestType.SMALL:
            return settings.APP_SMALL_CHEST_SECONDS
        case ChestType.LARGE | ChestType.EVENT:
            return settings.APP_LARGE_CHEST_SECONDS


def _seconds_left(latest: ChestClaim | None, chest_type: ChestType) -> int:
    if latest is None:
        return 0

    return max(_cooldown(chest_type) - clock.seconds_since(latest.claimed_at), 0)


def _roll(chest_type: ChestType) -> tuple[int, int, Shard, int]:
    """Returns `(orbs, diamonds, shard, demon_keys)`."""

    if chest_type is ChestType.SMALL:
        shard = (
            random.choice(_SHARDS)
            if random.random() < _SMALL_SHARD_CHANCE
            else Shard.NONE
        )

        return random.choice(_SMALL_ORBS), random.choice(_SMALL_DIAMONDS), shard, 0

    shard = (
        random.choice(_SHARDS) if random.random() < _LARGE_SHARD_CHANCE else Shard.NONE
    )
    keys = 1 if random.random() < _LARGE_KEY_CHANCE else 0

    return random.choice(_LARGE_ORBS), random.choice(_LARGE_DIAMONDS), shard, keys


async def _state(
    ctx: AbstractContext, user_id: int, chest_type: ChestType
) -> ChestState:
    latest = await ctx.chests.find_latest(user_id, chest_type)

    return ChestState(
        seconds_left=_seconds_left(latest, chest_type),
        contents=_wire.chest(latest),
        count=await ctx.chests.count(user_id, chest_type),
    )


async def _claim(
    ctx: AbstractContext, user_id: int, chest_type: ChestType
) -> RewardError.OnSuccess[ChestState]:
    latest = await ctx.chests.find_latest(user_id, chest_type)

    if _seconds_left(latest, chest_type) > 0:
        return RewardError.NOT_READY

    orbs, diamonds, shard, keys = _roll(chest_type)

    claim_id = await ctx.chests.create(
        user_id, chest_type, orbs=orbs, diamonds=diamonds, shard=shard, demon_keys=keys
    )
    logger.info(
        "Chest claimed.",
        extra={"user_id": user_id, "chest_type": int(chest_type), "claim_id": claim_id},
    )

    return ChestState(
        seconds_left=_cooldown(chest_type),
        contents=objects.Chest(orbs=orbs, diamonds=diamonds, shard=shard, keys=keys),
        count=await ctx.chests.count(user_id, chest_type),
    )


async def rewards(
    ctx: AbstractContext, session: Session, reward_type: RewardType
) -> RewardError.OnSuccess[RewardsPayload]:
    user_id = session.user.id

    if not await ctx.permissions.has(user_id, Permission.REWARDS_CLAIM):
        return RewardError.NOT_PERMITTED

    match reward_type:
        case RewardType.INFO:
            small = await _state(ctx, user_id, ChestType.SMALL)
            large = await _state(ctx, user_id, ChestType.LARGE)
        case RewardType.SMALL:
            claimed = await _claim(ctx, user_id, ChestType.SMALL)

            if is_error(claimed):
                return claimed

            small = claimed
            large = await _state(ctx, user_id, ChestType.LARGE)
        case RewardType.LARGE:
            claimed = await _claim(ctx, user_id, ChestType.LARGE)

            if is_error(claimed):
                return claimed

            small = await _state(ctx, user_id, ChestType.SMALL)
            large = claimed

    return RewardsPayload(small=small, large=large, reward_type=reward_type)


async def _assign_quests(ctx: AbstractContext, user_id: int) -> list[tuple[int, Quest]]:
    expires_at = clock.next_midnight()
    active = await ctx.quests.list_active()
    assigned: list[tuple[int, Quest]] = []

    for slot, item in enumerate(_QUEST_SLOTS, start=1):
        candidates = [quest for quest in active if quest.item is item]

        if not candidates:
            continue

        quest = random.choice(candidates)
        wire_id = await ctx.user_quests.assign(user_id, slot, quest.id, expires_at)
        assigned.append((wire_id, quest))

    return assigned


async def challenges(
    ctx: AbstractContext, session: Session
) -> RewardError.OnSuccess[ChallengesPayload]:
    user_id = session.user.id

    if not await ctx.permissions.has(user_id, Permission.QUESTS_VIEW):
        return RewardError.NOT_PERMITTED

    expires_at = clock.next_midnight()
    current = await ctx.user_quests.list_current(user_id, expires_at)
    assigned: list[tuple[int, Quest]] = []

    for entry in current:
        quest = await ctx.quests.find_by_id(entry.quest_id)

        if quest is not None:
            assigned.append((entry.id, quest))

    if len(assigned) < len(_QUEST_SLOTS):
        assigned = await _assign_quests(ctx, user_id)

    if len(assigned) < len(_QUEST_SLOTS):
        return RewardError.UNAVAILABLE

    first, second, third = (
        _wire.quest(quest, wire_id=wire_id) for wire_id, quest in assigned[:3]
    )

    return ChallengesPayload(
        quests=(first, second, third),
        seconds_left=clock.seconds_until(expires_at),
    )


async def _available(
    ctx: AbstractContext, reward: SecretReward, user_id: int
) -> RewardError | None:
    if reward.expires_at is not None and reward.expires_at <= clock.now():
        return RewardError.NOT_FOUND

    if await ctx.secret_rewards.has_claimed(reward.id, user_id):
        return RewardError.ALREADY_CLAIMED

    if (
        reward.max_claims is not None
        and await ctx.secret_rewards.count_claims(reward.id) >= reward.max_claims
    ):
        return RewardError.EXHAUSTED

    return None


async def secret_reward(
    ctx: AbstractContext, session: Session, reward_key: str
) -> RewardError.OnSuccess[SecretRewardPayload]:
    user_id = session.user.id

    if not await ctx.permissions.has(user_id, Permission.REWARDS_CLAIM):
        return RewardError.NOT_PERMITTED

    reward = await ctx.secret_rewards.find_by_key(reward_key.strip().lower())

    if reward is None:
        return RewardError.NOT_FOUND

    unavailable = await _available(ctx, reward, user_id)

    if unavailable is not None:
        return unavailable

    items = await ctx.secret_rewards.list_items(reward.id)
    await ctx.secret_rewards.create_claim(reward.id, user_id)
    logger.info(
        "Secret reward claimed.", extra={"user_id": user_id, "reward_id": reward.id}
    )

    return SecretRewardPayload(
        reward_id=reward.id,
        chest_type=reward.chest_type,
        rewards=tuple(objects.RewardStack(item.item, item.amount) for item in items),
    )

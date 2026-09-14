import json
from abc import ABC
from abc import abstractmethod
from typing import ClassVar
from typing import override

from gdformat.enums import Difficulty
from gdformat.enums import Rating
from gdformat.enums import TimelyType

from poltergeist_core.adapters.redis import RedisClient
from poltergeist_core.resources._common import Model
from poltergeist_core.resources.bans import BanType
from poltergeist_core.resources.flags import FlagKind
from poltergeist_core.resources.mod_actions import ModTarget
from poltergeist_core.utilities import clock

_CHANNEL_PREFIX = "poltergeist:"


class Event(Model):
    """Published on `poltergeist:<kind>` once the write it describes has been
    committed. Times are unix seconds; payloads never carry an IP address or a
    credential."""

    kind: ClassVar[str]

    @property
    def channel(self) -> str:
        return _CHANNEL_PREFIX + self.kind


class UserRegistered(Event):
    kind: ClassVar[str] = "users.registered"

    user_id: int
    username: str


class UserRenamed(Event):
    kind: ClassVar[str] = "users.renamed"

    user_id: int
    old_username: str
    new_username: str
    actor_user_id: int


class UserBanned(Event):
    kind: ClassVar[str] = "users.banned"

    ban_id: int
    user_id: int
    username: str
    ban_type: BanType
    reason: str
    days: int | None
    expires_at: int | None
    actor_user_id: int


class UserUnbanned(Event):
    kind: ClassVar[str] = "users.unbanned"

    user_id: int
    username: str
    ban_type: BanType
    revoked: int
    actor_user_id: int


class UserFlagged(Event):
    """`summary` is the evidence in one line; the row holds the detail."""

    kind: ClassVar[str] = "users.flagged"

    flag_id: int
    user_id: int
    username: str
    flag_kind: FlagKind
    summary: str


class LevelUploaded(Event):
    kind: ClassVar[str] = "levels.uploaded"

    level_id: int
    level_name: str
    user_id: int
    username: str
    version: int


class LevelUpdated(Event):
    kind: ClassVar[str] = "levels.updated"

    level_id: int
    level_name: str
    user_id: int
    username: str
    version: int


class LevelDeleted(Event):
    kind: ClassVar[str] = "levels.deleted"

    level_id: int
    level_name: str
    user_id: int
    actor_user_id: int


class LevelRated(Event):
    kind: ClassVar[str] = "levels.rated"

    level_id: int
    level_name: str
    user_id: int
    username: str
    stars: int
    difficulty: Difficulty
    rating: Rating
    feature_order: int
    actor_user_id: int


class TimelyScheduled(Event):
    kind: ClassVar[str] = "timely.scheduled"

    timely_id: int
    timely_type: TimelyType
    sequence: int
    level_id: int
    level_name: str
    starts_at: int
    ends_at: int
    actor_user_id: int


class RoleAssigned(Event):
    kind: ClassVar[str] = "roles.assigned"

    user_id: int
    username: str
    role_id: int
    role_name: str
    expires_at: int | None
    actor_user_id: int


class RoleRevoked(Event):
    kind: ClassVar[str] = "roles.revoked"

    user_id: int
    role_id: int
    role_name: str
    actor_user_id: int


class ServerSettingsUpdated(Event):
    kind: ClassVar[str] = "server_settings.updated"

    changes: dict[str, object]
    actor_user_id: int


class LeaderboardsRebuilt(Event):
    kind: ClassVar[str] = "leaderboards.rebuilt"

    users: int


class ModerationAction(Event):
    """One per `mod_actions` row, so every administrative action reaches the
    bus even when it has no typed event."""

    kind: ClassVar[str] = "moderation.action"

    mod_action_id: int
    actor_user_id: int
    action: str
    target_type: ModTarget
    target_id: int
    details: dict[str, object] | None


class ImplementsEventPublisher(ABC):
    @abstractmethod
    async def publish(self, event: Event) -> None: ...


class EventPublisher(ImplementsEventPublisher):
    """Publishes as soon as it is called, so it is only correct outside a
    transaction."""

    __slots__ = ("_component", "_redis")  # Built on every context property access.

    def __init__(self, redis: RedisClient, *, component: str) -> None:
        self._redis = redis
        self._component = component

    @override
    async def publish(self, event: Event) -> None:
        envelope = {
            "event": event.kind,
            "component": self._component,
            "emitted_at": clock.timestamp(clock.now()),
            "data": event.model_dump(mode="json"),
        }

        await self._redis.publish(event.channel, json.dumps(envelope))


class EventOutbox(ImplementsEventPublisher):
    """Holds events until the owner awaits `flush` after the transaction
    commits, so a rolled-back write emits nothing."""

    __slots__ = ("_pending", "_publisher")  # Built for every writing request.

    def __init__(self, publisher: ImplementsEventPublisher) -> None:
        self._publisher = publisher
        self._pending: list[Event] = []

    @override
    async def publish(self, event: Event) -> None:
        self._pending.append(event)

    async def flush(self) -> None:
        for event in self._pending:
            await self._publisher.publish(event)

        self._pending.clear()

import json
from typing import Any

from poltergeist_core.adapters.redis import RedisClient
from poltergeist_core.resources._common import Model

KEY = "events:recent"
# Roughly a day of traffic at the target scale; trimming is approximate.
_LENGTH = 2000


class ActivityEntry(Model):
    """One published event as kept in the recent-events stream. `stream_id`
    orders entries and is what a reader resumes from."""

    stream_id: str
    event: str
    component: str
    emitted_at: int
    data: dict[str, Any]


def _entries(reply: object) -> list[ActivityEntry]:
    if not isinstance(reply, list):
        return []

    entries = []

    for stream_id, fields in reply:
        values = dict(zip(fields[::2], fields[1::2], strict=False))
        entry = _entry(stream_id, values.get("envelope"))

        if entry is not None:
            entries.append(entry)

    return entries


def _entry(stream_id: str, envelope: object) -> ActivityEntry | None:
    """Anyone who can reach Redis can append to the stream, so an entry that
    is not a valid envelope is skipped instead of failing every reader."""

    if not isinstance(envelope, str):
        return None

    try:
        parsed = json.loads(envelope)

        return ActivityEntry.model_validate({**parsed, "stream_id": stream_id})
    # JSON and Pydantic both signal a bad document with ValueError subclasses;
    # a TypeError covers a document that is not an object.
    except ValueError, TypeError:
        return None


class ActivityRepository:
    """The tail of the event bus as a capped Redis stream, so a reader that
    was not subscribed when something happened can still show it."""

    __slots__ = ("_redis",)  # Built on every context property access.

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def latest(self, count: int) -> list[ActivityEntry]:
        """Newest first."""

        return _entries(
            await self._redis.execute_command(
                "XREVRANGE", KEY, "+", "-", "COUNT", count
            )
        )

    async def after(self, stream_id: str, count: int) -> list[ActivityEntry]:
        """Oldest first, strictly after `stream_id`."""

        return _entries(
            await self._redis.execute_command(
                "XRANGE", KEY, f"({stream_id}", "+", "COUNT", count
            )
        )


def append_command(envelope: str) -> tuple[str, ...]:
    """The XADD that records an envelope, for the publisher's pipeline."""

    return ("XADD", KEY, "MAXLEN", "~", str(_LENGTH), "*", "envelope", envelope)

from datetime import UTC
from datetime import datetime
from datetime import timedelta

_DAY = timedelta(days=1)


def now() -> datetime:
    """The current UTC time as a naive whole second, matching what MySQL stores
    and returns for DATETIME columns on a `+00:00` session. Microseconds are
    dropped rather than rounded so a value written now is never in the future."""

    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def timestamp(moment: datetime) -> int:
    return int(moment.replace(tzinfo=UTC).timestamp())


def seconds_since(moment: datetime) -> int:
    return max(int((now() - moment).total_seconds()), 0)


def seconds_until(moment: datetime) -> int:
    return max(int((moment - now()).total_seconds()), 0)


def next_midnight() -> datetime:
    today = now().replace(hour=0, minute=0, second=0, microsecond=0)

    return today + _DAY

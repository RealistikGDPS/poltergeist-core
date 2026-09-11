import json
from collections.abc import Sequence
from typing import Annotated
from typing import Any

from pydantic import BaseModel
from pydantic import BeforeValidator
from pydantic import ConfigDict

from poltergeist_core.adapters.mysql import MySQLValues


class Model(BaseModel):
    model_config = ConfigDict(frozen=True)


def _parse_json(value: Any) -> Any:
    if isinstance(value, str | bytes):
        return json.loads(value)

    return value


type JsonIntList = Annotated[list[int], BeforeValidator(_parse_json)]
type JsonObject = Annotated[dict[str, Any] | None, BeforeValidator(_parse_json)]


def placeholders(values: Sequence[int], prefix: str) -> tuple[str, MySQLValues]:
    """Builds `%(p0)s, %(p1)s, ...` and the values to bind for an `IN` clause."""

    names = [f"{prefix}{index}" for index in range(len(values))]
    sql = ", ".join(f"%({name})s" for name in names)

    return sql, dict(zip(names, values, strict=True))


def offset(page: int, size: int) -> int:
    return max(page, 0) * size

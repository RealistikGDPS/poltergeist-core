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


def page_sql(
    table: str, columns: str, wheres: Sequence[str], order: str, reach: int
) -> str:
    """One page of `table l`, cut by `%(limit)s` and `%(offset)s`. Several
    `wheres` are alternatives: each takes its own first `reach` rows, so each
    can walk an index in order, and the page is cut from their union."""

    if len(wheres) == 1:
        return (
            f"SELECT {columns} FROM {table} l WHERE {wheres[0]} ORDER BY {order} "
            "LIMIT %(limit)s OFFSET %(offset)s"
        )

    ids = " UNION ".join(
        f"(SELECT l.id FROM {table} l WHERE {where} ORDER BY {order} LIMIT {reach})"
        for where in wheres
    )

    return (
        f"SELECT {columns} FROM ({ids}) page JOIN {table} l ON l.id = page.id "
        f"ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s"
    )


def disjoint(branches: Sequence[str]) -> list[str]:
    """Narrows each alternative to the rows no earlier one matches, so that
    their counts add up."""

    return [
        "("
        + " AND ".join([branch, *(f"NOT ({other})" for other in branches[:index])])
        + ")"
        for index, branch in enumerate(branches)
    ]


def capped_count_sql(table: str, wheres: Sequence[str], cap: int) -> str:
    """One `total` row per alternative, each reading at most `cap` rows."""

    return " UNION ALL ".join(
        f"SELECT COUNT(*) AS total FROM (SELECT 1 FROM {table} l WHERE {where} "
        f"LIMIT {cap}) c"
        for where in wheres
    )

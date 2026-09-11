from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from gdformat.enums import Difficulty
from gdformat.enums import Visibility

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.adapters.mysql import MySQLValue
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset
from poltergeist_core.resources._common import placeholders
from poltergeist_core.utilities import clock

_COLUMNS = (
    "l.id, l.user_id, l.name, l.description, l.version, l.difficulty, l.visibility, "
    "l.original_id, l.downloads, l.likes, l.rated_at, l.rated_by_user_id, "
    "l.reward_diamonds, l.reward_requirement, l.uploaded_at, l.updated_at"
)
_COUNT_CAP = 9999


class ListOrder(StrEnum):
    DOWNLOADS = "downloads"
    LIKES = "likes"
    UPLOADED = "uploaded"
    RATED = "rated"
    SUGGESTED = "suggested"


class LevelList(Model):
    id: int
    user_id: int
    name: str
    description: str
    version: int
    difficulty: Difficulty
    visibility: Visibility
    original_id: int | None
    downloads: int
    likes: int
    rated_at: datetime | None
    rated_by_user_id: int | None
    reward_diamonds: int
    reward_requirement: int
    uploaded_at: datetime
    updated_at: datetime

    @property
    def is_rated(self) -> bool:
        return self.rated_at is not None


@dataclass(frozen=True, slots=True, kw_only=True)
class ListSearch:
    order: ListOrder
    page: int
    size: int
    viewer_user_id: int | None = None
    friend_ids: tuple[int, ...] = ()
    include_unlisted: bool = False
    include_all_visibilities: bool = False
    list_ids: tuple[int, ...] | None = None
    creator_ids: tuple[int, ...] | None = None
    name_prefix: str | None = None
    difficulties: tuple[Difficulty, ...] | None = None
    rated: bool = False
    uploaded_after: datetime | None = None


def _order_sql(order: ListOrder) -> str:
    match order:
        case ListOrder.DOWNLOADS:
            return "l.downloads DESC, l.id DESC"
        case ListOrder.LIKES:
            return "l.likes DESC, l.id DESC"
        case ListOrder.UPLOADED:
            return "l.uploaded_at DESC, l.id DESC"
        case ListOrder.RATED:
            return "l.rated_at DESC, l.id DESC"
        case ListOrder.SUGGESTED:
            return "l.updated_at DESC, l.id DESC"


def _where_sql(search: ListSearch) -> tuple[str, dict[str, MySQLValue]]:
    values: dict[str, MySQLValue] = {}
    clauses = ["l.deleted_at IS NULL"]

    if not search.include_all_visibilities:
        parts = [f"l.visibility = {int(Visibility.PUBLIC)}"]

        if search.include_unlisted:
            parts.append(f"l.visibility = {int(Visibility.UNLISTED)}")

        if search.friend_ids:
            sql, friends = placeholders(list(search.friend_ids), "friend")
            values.update(friends)
            parts.append(
                f"(l.visibility = {int(Visibility.FRIENDS)} AND l.user_id IN ({sql}))"
            )

        if search.viewer_user_id is not None:
            values["viewer"] = search.viewer_user_id
            parts.append("l.user_id = %(viewer)s")

        clauses.append("(" + " OR ".join(parts) + ")")

    if search.list_ids is not None:
        if not search.list_ids:
            clauses.append("FALSE")
        else:
            sql, given = placeholders(list(search.list_ids), "list")
            values.update(given)
            clauses.append(f"l.id IN ({sql})")

    if search.creator_ids is not None:
        if not search.creator_ids:
            clauses.append("FALSE")
        else:
            sql, given = placeholders(list(search.creator_ids), "creator")
            values.update(given)
            clauses.append(f"l.user_id IN ({sql})")

    if search.name_prefix is not None:
        values["name_pattern"] = f"{search.name_prefix}%"
        clauses.append("l.name LIKE %(name_pattern)s")

    if search.difficulties is not None:
        sql, given = placeholders([int(d) for d in search.difficulties], "difficulty")
        values.update(given)
        clauses.append(f"l.difficulty IN ({sql})")

    if search.rated:
        clauses.append("l.rated_at IS NOT NULL")

    if search.uploaded_after is not None:
        values["uploaded_after"] = search.uploaded_after
        clauses.append("l.uploaded_at >= %(uploaded_after)s")

    return " AND ".join(clauses), values


class LevelListRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_id(self, list_id: int) -> LevelList | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM level_lists l WHERE l.id = %(id)s "
            "AND l.deleted_at IS NULL",
            {"id": list_id},
        )

        return None if row is None else LevelList.model_validate(row)

    async def find_by_user_and_name(self, user_id: int, name: str) -> LevelList | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM level_lists l WHERE l.user_id = %(user)s "
            "AND l.name = %(name)s AND l.deleted_at IS NULL ORDER BY l.id DESC LIMIT 1",
            {"user": user_id, "name": name},
        )

        return None if row is None else LevelList.model_validate(row)

    async def search(self, search: ListSearch) -> list[LevelList]:
        where, values = _where_sql(search)

        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM level_lists l WHERE {where} "
            f"ORDER BY {_order_sql(search.order)} LIMIT %(limit)s OFFSET %(offset)s",
            {
                **values,
                "limit": search.size,
                "offset": offset(search.page, search.size),
            },
        )

        return [LevelList.model_validate(row) for row in rows]

    async def count(self, search: ListSearch) -> int:
        where, values = _where_sql(search)

        count: int = await self._mysql.fetch_val(
            f"SELECT COUNT(*) FROM (SELECT 1 FROM level_lists l WHERE {where} "
            f"LIMIT {_COUNT_CAP}) c",
            values,
        )

        return count

    async def list_level_ids(self, list_id: int) -> list[int]:
        rows = await self._mysql.fetch_all(
            "SELECT level_id FROM level_list_levels WHERE list_id = %(id)s "
            "ORDER BY position",
            {"id": list_id},
        )

        return [int(row["level_id"]) for row in rows]

    async def list_level_ids_many(self, list_ids: list[int]) -> dict[int, list[int]]:
        if not list_ids:
            return {}

        sql, values = placeholders(list_ids, "id")

        rows = await self._mysql.fetch_all(
            f"SELECT list_id, level_id FROM level_list_levels WHERE list_id IN ({sql}) "
            "ORDER BY list_id, position",
            values,
        )

        levels: dict[int, list[int]] = {list_id: [] for list_id in list_ids}

        for row in rows:
            levels[int(row["list_id"])].append(int(row["level_id"]))

        return levels

    async def create(
        self,
        *,
        user_id: int,
        name: str,
        description: str,
        version: int,
        difficulty: Difficulty,
        visibility: Visibility,
        original_id: int | None,
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO level_lists (user_id, name, description, version, "
            "difficulty, visibility, original_id) VALUES (%(user)s, %(name)s, "
            "%(description)s, %(version)s, %(difficulty)s, %(visibility)s, "
            "%(original)s)",
            {
                "user": user_id,
                "name": name,
                "description": description,
                "version": version,
                "difficulty": int(difficulty),
                "visibility": int(visibility),
                "original": original_id,
            },
        )

        return result.last_row_id

    async def update(
        self,
        list_id: int,
        *,
        name: str,
        description: str,
        version: int,
        difficulty: Difficulty,
        visibility: Visibility,
    ) -> None:
        await self._mysql.execute(
            "UPDATE level_lists SET name = %(name)s, description = %(description)s, "
            "version = %(version)s, difficulty = %(difficulty)s, "
            "visibility = %(visibility)s, updated_at = %(now)s WHERE id = %(id)s",
            {
                "id": list_id,
                "name": name,
                "description": description,
                "version": version,
                "difficulty": int(difficulty),
                "visibility": int(visibility),
                "now": clock.now(),
            },
        )

    async def replace_levels(self, list_id: int, level_ids: list[int]) -> None:
        await self._mysql.execute(
            "DELETE FROM level_list_levels WHERE list_id = %(id)s",
            {"id": list_id},
        )

        for position, level_id in enumerate(level_ids):
            await self._mysql.execute(
                "INSERT INTO level_list_levels (list_id, position, level_id) "
                "VALUES (%(list)s, %(position)s, %(level)s)",
                {"list": list_id, "position": position, "level": level_id},
            )

    async def soft_delete(self, list_id: int) -> None:
        await self._mysql.execute(
            "UPDATE level_lists SET deleted_at = %(now)s WHERE id = %(id)s",
            {"id": list_id, "now": clock.now()},
        )

    async def increment_downloads(self, list_id: int) -> None:
        await self._mysql.execute(
            "UPDATE level_lists SET downloads = downloads + 1 WHERE id = %(id)s",
            {"id": list_id},
        )

    async def add_likes(self, list_id: int, delta: int) -> None:
        await self._mysql.execute(
            "UPDATE level_lists SET likes = likes + %(delta)s WHERE id = %(id)s",
            {"id": list_id, "delta": delta},
        )

    async def rate(
        self,
        list_id: int,
        *,
        rated: bool,
        reward_diamonds: int,
        reward_requirement: int,
        rated_by_user_id: int | None,
    ) -> None:
        await self._mysql.execute(
            "UPDATE level_lists SET rated_at = %(rated_at)s, "
            "rated_by_user_id = %(rated_by)s, reward_diamonds = %(diamonds)s, "
            "reward_requirement = %(requirement)s WHERE id = %(id)s",
            {
                "id": list_id,
                "rated_at": clock.now() if rated else None,
                "rated_by": rated_by_user_id if rated else None,
                "diamonds": reward_diamonds,
                "requirement": reward_requirement,
            },
        )

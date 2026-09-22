from datetime import datetime
from enum import StrEnum

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset
from poltergeist_core.resources._common import placeholders
from poltergeist_core.utilities import clock

_PLACEMENT_COLUMNS = (
    "id, level_id, position, requirement, video_url, added_by_user_id, created_at, "
    "updated_at, deleted_at"
)
_RECORD_COLUMNS = (
    "id, placement_id, user_id, percent, status, video_url, raw_footage_url, notes, "
    "review_note, submitted_at, reviewed_at, reviewed_by_user_id, deleted_at"
)


class RecordStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    # An approved record replaced by a later approval of the same player.
    SUPERSEDED = "superseded"


class DemonListPlacement(Model):
    id: int
    level_id: int
    position: int
    requirement: int
    video_url: str
    added_by_user_id: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class DemonListRecord(Model):
    id: int
    placement_id: int
    user_id: int
    percent: int
    status: RecordStatus
    video_url: str
    raw_footage_url: str
    notes: str
    review_note: str
    submitted_at: datetime
    reviewed_at: datetime | None
    reviewed_by_user_id: int | None
    deleted_at: datetime | None


class RankedRecord(Model):
    """An approved record joined to its live placement, enough to score it."""

    user_id: int
    placement_id: int
    position: int
    requirement: int
    percent: int


class DemonListPlacementRepository:
    """Positions of live placements are contiguous from 1; the service keeps
    them so through `shift`. A removed level keeps its row so re-adding it
    revives its records."""

    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_id(self, placement_id: int) -> DemonListPlacement | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_PLACEMENT_COLUMNS} FROM demon_list_placements "
            "WHERE id = %(id)s AND deleted_at IS NULL",
            {"id": placement_id},
        )

        return None if row is None else DemonListPlacement.model_validate(row)

    async def find_by_level(
        self, level_id: int, *, include_removed: bool = False
    ) -> DemonListPlacement | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_PLACEMENT_COLUMNS} FROM demon_list_placements "
            "WHERE level_id = %(level)s "
            "AND (%(removed)s OR deleted_at IS NULL)",
            {"level": level_id, "removed": include_removed},
        )

        return None if row is None else DemonListPlacement.model_validate(row)

    async def find_many_by_ids(
        self, placement_ids: list[int]
    ) -> list[DemonListPlacement]:
        if not placement_ids:
            return []

        sql, values = placeholders(placement_ids, "p")

        rows = await self._mysql.fetch_all(
            f"SELECT {_PLACEMENT_COLUMNS} FROM demon_list_placements "
            f"WHERE id IN ({sql})",
            values,
        )

        return [DemonListPlacement.model_validate(row) for row in rows]

    async def list_all(self) -> list[DemonListPlacement]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_PLACEMENT_COLUMNS} FROM demon_list_placements "
            "WHERE deleted_at IS NULL ORDER BY position"
        )

        return [DemonListPlacement.model_validate(row) for row in rows]

    async def count(self) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM demon_list_placements WHERE deleted_at IS NULL"
        )

        return count

    async def create(
        self,
        level_id: int,
        *,
        position: int,
        requirement: int,
        video_url: str,
        added_by_user_id: int,
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO demon_list_placements (level_id, position, requirement, "
            "video_url, added_by_user_id, created_at, updated_at) "
            "VALUES (%(level)s, %(position)s, %(requirement)s, %(video)s, %(by)s, "
            "%(now)s, %(now)s)",
            {
                "level": level_id,
                "position": position,
                "requirement": requirement,
                "video": video_url,
                "by": added_by_user_id,
                "now": clock.now(),
            },
        )

        return result.last_row_id

    async def revive(
        self,
        placement_id: int,
        *,
        position: int,
        requirement: int,
        video_url: str,
        added_by_user_id: int,
    ) -> None:
        await self._mysql.execute(
            "UPDATE demon_list_placements SET position = %(position)s, "
            "requirement = %(requirement)s, video_url = %(video)s, "
            "added_by_user_id = %(by)s, updated_at = %(now)s, deleted_at = NULL "
            "WHERE id = %(id)s",
            {
                "id": placement_id,
                "position": position,
                "requirement": requirement,
                "video": video_url,
                "by": added_by_user_id,
                "now": clock.now(),
            },
        )

    async def shift(self, *, low: int, high: int, delta: int) -> None:
        """Moves every live placement positioned in `low..high` by `delta`."""

        await self._mysql.execute(
            "UPDATE demon_list_placements SET position = position + %(delta)s "
            "WHERE deleted_at IS NULL AND position BETWEEN %(low)s AND %(high)s",
            {"low": low, "high": high, "delta": delta},
        )

    async def set_position(self, placement_id: int, position: int) -> None:
        await self._mysql.execute(
            "UPDATE demon_list_placements SET position = %(position)s, "
            "updated_at = %(now)s WHERE id = %(id)s",
            {"id": placement_id, "position": position, "now": clock.now()},
        )

    async def update(
        self, placement_id: int, *, requirement: int, video_url: str
    ) -> None:
        await self._mysql.execute(
            "UPDATE demon_list_placements SET requirement = %(requirement)s, "
            "video_url = %(video)s, updated_at = %(now)s WHERE id = %(id)s",
            {
                "id": placement_id,
                "requirement": requirement,
                "video": video_url,
                "now": clock.now(),
            },
        )

    async def soft_delete(self, placement_id: int) -> None:
        await self._mysql.execute(
            "UPDATE demon_list_placements SET deleted_at = %(now)s, "
            "updated_at = %(now)s WHERE id = %(id)s",
            {"id": placement_id, "now": clock.now()},
        )


class DemonListRecordRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def create(
        self,
        placement_id: int,
        user_id: int,
        *,
        percent: int,
        status: RecordStatus,
        video_url: str,
        raw_footage_url: str,
        notes: str,
        reviewed_by_user_id: int | None,
    ) -> int:
        now = clock.now()

        result = await self._mysql.execute(
            "INSERT INTO demon_list_records (placement_id, user_id, percent, status, "
            "video_url, raw_footage_url, notes, submitted_at, reviewed_at, "
            "reviewed_by_user_id) VALUES (%(placement)s, %(user)s, %(percent)s, "
            "%(status)s, %(video)s, %(raw)s, %(notes)s, %(now)s, %(reviewed_at)s, "
            "%(by)s)",
            {
                "placement": placement_id,
                "user": user_id,
                "percent": percent,
                "status": status.value,
                "video": video_url,
                "raw": raw_footage_url,
                "notes": notes,
                "now": now,
                "reviewed_at": None if reviewed_by_user_id is None else now,
                "by": reviewed_by_user_id,
            },
        )

        return result.last_row_id

    async def find_by_id(self, record_id: int) -> DemonListRecord | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_RECORD_COLUMNS} FROM demon_list_records "
            "WHERE id = %(id)s AND deleted_at IS NULL",
            {"id": record_id},
        )

        return None if row is None else DemonListRecord.model_validate(row)

    async def find_many_by_ids(self, record_ids: list[int]) -> list[DemonListRecord]:
        if not record_ids:
            return []

        sql, values = placeholders(record_ids, "r")

        rows = await self._mysql.fetch_all(
            f"SELECT {_RECORD_COLUMNS} FROM demon_list_records WHERE id IN ({sql}) "
            "AND deleted_at IS NULL",
            values,
        )

        return [DemonListRecord.model_validate(row) for row in rows]

    async def _find_by_status(
        self, placement_id: int, user_id: int, status: RecordStatus
    ) -> DemonListRecord | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_RECORD_COLUMNS} FROM demon_list_records "
            "WHERE placement_id = %(placement)s AND user_id = %(user)s "
            "AND status = %(status)s AND deleted_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            {"placement": placement_id, "user": user_id, "status": status.value},
        )

        return None if row is None else DemonListRecord.model_validate(row)

    async def find_pending(
        self, placement_id: int, user_id: int
    ) -> DemonListRecord | None:
        return await self._find_by_status(placement_id, user_id, RecordStatus.PENDING)

    async def find_approved(
        self, placement_id: int, user_id: int
    ) -> DemonListRecord | None:
        return await self._find_by_status(
            placement_id, user_id, RecordStatus.APPROVED
        )

    async def list_approved_by_placement(
        self, placement_id: int
    ) -> list[DemonListRecord]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_RECORD_COLUMNS} FROM demon_list_records "
            "WHERE placement_id = %(placement)s AND status = 'approved' "
            "AND deleted_at IS NULL ORDER BY percent DESC, submitted_at, id",
            {"placement": placement_id},
        )

        return [DemonListRecord.model_validate(row) for row in rows]

    async def list_approved_by_placements(
        self, placement_ids: list[int]
    ) -> list[DemonListRecord]:
        if not placement_ids:
            return []

        sql, values = placeholders(placement_ids, "p")

        rows = await self._mysql.fetch_all(
            f"SELECT {_RECORD_COLUMNS} FROM demon_list_records "
            f"WHERE placement_id IN ({sql}) AND status = 'approved' "
            "AND deleted_at IS NULL",
            values,
        )

        return [DemonListRecord.model_validate(row) for row in rows]

    async def list_approved_by_user(self, user_id: int) -> list[DemonListRecord]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_RECORD_COLUMNS} FROM demon_list_records "
            "WHERE user_id = %(user)s AND status = 'approved' AND deleted_at IS NULL "
            "ORDER BY id DESC",
            {"user": user_id},
        )

        return [DemonListRecord.model_validate(row) for row in rows]

    async def list_ranked(self) -> list[RankedRecord]:
        rows = await self._mysql.fetch_all(
            "SELECT r.user_id, r.placement_id, p.position, p.requirement, r.percent "
            "FROM demon_list_records r "
            "JOIN demon_list_placements p ON p.id = r.placement_id "
            "WHERE r.status = 'approved' AND r.deleted_at IS NULL "
            "AND p.deleted_at IS NULL"
        )

        return [RankedRecord.model_validate(row) for row in rows]

    async def list_page(
        self,
        *,
        status: RecordStatus | None,
        placement_id: int | None,
        user_id: int | None,
        page: int,
        size: int,
    ) -> list[DemonListRecord]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_RECORD_COLUMNS} FROM demon_list_records "
            "WHERE deleted_at IS NULL "
            "AND (%(status)s IS NULL OR status = %(status)s) "
            "AND (%(placement)s IS NULL OR placement_id = %(placement)s) "
            "AND (%(user)s IS NULL OR user_id = %(user)s) "
            "ORDER BY id DESC LIMIT %(limit)s OFFSET %(offset)s",
            {
                "status": None if status is None else status.value,
                "placement": placement_id,
                "user": user_id,
                "limit": size,
                "offset": offset(page, size),
            },
        )

        return [DemonListRecord.model_validate(row) for row in rows]

    async def count_page(
        self,
        *,
        status: RecordStatus | None,
        placement_id: int | None,
        user_id: int | None,
    ) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM demon_list_records WHERE deleted_at IS NULL "
            "AND (%(status)s IS NULL OR status = %(status)s) "
            "AND (%(placement)s IS NULL OR placement_id = %(placement)s) "
            "AND (%(user)s IS NULL OR user_id = %(user)s)",
            {
                "status": None if status is None else status.value,
                "placement": placement_id,
                "user": user_id,
            },
        )

        return count

    async def count_by_status(
        self, placement_ids: list[int]
    ) -> dict[int, dict[RecordStatus, int]]:
        if not placement_ids:
            return {}

        sql, values = placeholders(placement_ids, "p")

        rows = await self._mysql.fetch_all(
            "SELECT placement_id, status, COUNT(*) AS total FROM demon_list_records "
            f"WHERE placement_id IN ({sql}) AND deleted_at IS NULL "
            "GROUP BY placement_id, status",
            values,
        )
        counts: dict[int, dict[RecordStatus, int]] = {}

        for row in rows:
            placement = counts.setdefault(int(row["placement_id"]), {})
            placement[RecordStatus(row["status"])] = int(row["total"])

        return counts

    async def review(
        self,
        record_id: int,
        status: RecordStatus,
        *,
        reviewed_by_user_id: int,
        review_note: str,
    ) -> None:
        await self._mysql.execute(
            "UPDATE demon_list_records SET status = %(status)s, "
            "review_note = %(note)s, reviewed_at = %(now)s, "
            "reviewed_by_user_id = %(by)s WHERE id = %(id)s",
            {
                "id": record_id,
                "status": status.value,
                "note": review_note,
                "now": clock.now(),
                "by": reviewed_by_user_id,
            },
        )

    async def supersede(
        self, placement_id: int, user_id: int, *, except_record_id: int
    ) -> None:
        await self._mysql.execute(
            "UPDATE demon_list_records SET status = 'superseded' "
            "WHERE placement_id = %(placement)s AND user_id = %(user)s "
            "AND status = 'approved' AND id != %(keep)s AND deleted_at IS NULL",
            {"placement": placement_id, "user": user_id, "keep": except_record_id},
        )

    async def soft_delete(self, record_id: int) -> None:
        await self._mysql.execute(
            "UPDATE demon_list_records SET deleted_at = %(now)s WHERE id = %(id)s",
            {"id": record_id, "now": clock.now()},
        )

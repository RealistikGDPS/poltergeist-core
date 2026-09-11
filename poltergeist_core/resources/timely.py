from datetime import datetime

from gdformat.enums import ChestType
from gdformat.enums import RewardItem
from gdformat.enums import TimelyType

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset
from poltergeist_core.utilities import clock

_COLUMNS = (
    "id, type, sequence, level_id, starts_at, ends_at, chest_type, scheduled_by_user_id"
)
_PREFIXED_COLUMNS = ", ".join(f"t.{column}" for column in _COLUMNS.split(", "))


class TimelyLevel(Model):
    id: int
    type: TimelyType
    sequence: int
    level_id: int
    starts_at: datetime
    ends_at: datetime
    chest_type: ChestType | None
    scheduled_by_user_id: int | None

    @property
    def wire_id(self) -> int:
        """The id the client sees: the sequence offset by the timely type."""

        return self.sequence + self.type.id_offset


class TimelyReward(Model):
    item: RewardItem
    amount: int


class TimelyRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_current(self, timely_type: TimelyType) -> TimelyLevel | None:
        """The entry whose window covers now, skipping ones whose level is gone."""

        now = clock.now()

        row = await self._mysql.fetch_one(
            f"SELECT {_PREFIXED_COLUMNS} FROM timely_levels t "
            "WHERE t.type = %(type)s AND t.deleted_at IS NULL "
            "AND t.starts_at <= %(now)s AND t.ends_at > %(now)s "
            "AND EXISTS (SELECT 1 FROM levels l WHERE l.id = t.level_id "
            "AND l.deleted_at IS NULL) ORDER BY t.starts_at DESC LIMIT 1",
            {"type": int(timely_type), "now": now},
        )

        return None if row is None else TimelyLevel.model_validate(row)

    async def find_by_id(self, timely_id: int) -> TimelyLevel | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM timely_levels WHERE id = %(id)s "
            "AND deleted_at IS NULL",
            {"id": timely_id},
        )

        return None if row is None else TimelyLevel.model_validate(row)

    async def find_by_sequence(
        self, timely_type: TimelyType, sequence: int
    ) -> TimelyLevel | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM timely_levels WHERE type = %(type)s "
            "AND sequence = %(sequence)s AND deleted_at IS NULL",
            {"type": int(timely_type), "sequence": sequence},
        )

        return None if row is None else TimelyLevel.model_validate(row)

    async def find_last(self, timely_type: TimelyType) -> TimelyLevel | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM timely_levels WHERE type = %(type)s "
            "AND deleted_at IS NULL ORDER BY sequence DESC LIMIT 1",
            {"type": int(timely_type)},
        )

        return None if row is None else TimelyLevel.model_validate(row)

    async def list_from(
        self, timely_type: TimelyType, page: int, size: int
    ) -> list[TimelyLevel]:
        """Newest first, so the queue and the history read as one list."""

        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM timely_levels WHERE type = %(type)s "
            "AND deleted_at IS NULL ORDER BY sequence DESC "
            "LIMIT %(limit)s OFFSET %(offset)s",
            {"type": int(timely_type), "limit": size, "offset": offset(page, size)},
        )

        return [TimelyLevel.model_validate(row) for row in rows]

    async def create(
        self,
        *,
        timely_type: TimelyType,
        sequence: int,
        level_id: int,
        starts_at: datetime,
        ends_at: datetime,
        chest_type: ChestType | None,
        scheduled_by_user_id: int | None,
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO timely_levels (type, sequence, level_id, starts_at, ends_at, "
            "chest_type, scheduled_by_user_id) VALUES (%(type)s, %(sequence)s, "
            "%(level)s, %(starts)s, %(ends)s, %(chest)s, %(by)s)",
            {
                "type": int(timely_type),
                "sequence": sequence,
                "level": level_id,
                "starts": starts_at,
                "ends": ends_at,
                "chest": None if chest_type is None else int(chest_type),
                "by": scheduled_by_user_id,
            },
        )

        return result.last_row_id

    async def add_reward(self, timely_id: int, item: RewardItem, amount: int) -> None:
        await self._mysql.execute(
            "INSERT INTO timely_level_rewards (timely_level_id, item, amount) "
            "VALUES (%(id)s, %(item)s, %(amount)s) "
            "ON DUPLICATE KEY UPDATE amount = VALUES(amount)",
            {"id": timely_id, "item": int(item), "amount": amount},
        )

    async def list_rewards(self, timely_id: int) -> list[TimelyReward]:
        rows = await self._mysql.fetch_all(
            "SELECT item, amount FROM timely_level_rewards "
            "WHERE timely_level_id = %(id)s ORDER BY item",
            {"id": timely_id},
        )

        return [TimelyReward.model_validate(row) for row in rows]

    async def soft_delete(self, timely_id: int) -> None:
        await self._mysql.execute(
            "UPDATE timely_levels SET deleted_at = %(now)s WHERE id = %(id)s",
            {"id": timely_id, "now": clock.now()},
        )

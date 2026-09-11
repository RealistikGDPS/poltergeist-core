from datetime import datetime

from gdformat.enums import QuestItem

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.utilities import clock


class Quest(Model):
    id: int
    item: QuestItem
    amount: int
    diamonds: int
    name: str


class UserQuest(Model):
    id: int
    user_id: int
    slot: int
    quest_id: int
    assigned_at: datetime
    expires_at: datetime


class QuestRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def list_active(self) -> list[Quest]:
        rows = await self._mysql.fetch_all(
            "SELECT id, item, amount, diamonds, name FROM quests "
            "WHERE deleted_at IS NULL ORDER BY item, amount"
        )

        return [Quest.model_validate(row) for row in rows]

    async def find_by_id(self, quest_id: int) -> Quest | None:
        row = await self._mysql.fetch_one(
            "SELECT id, item, amount, diamonds, name FROM quests WHERE id = %(id)s "
            "AND deleted_at IS NULL",
            {"id": quest_id},
        )

        return None if row is None else Quest.model_validate(row)

    async def soft_delete(self, quest_id: int) -> None:
        await self._mysql.execute(
            "UPDATE quests SET deleted_at = %(now)s WHERE id = %(id)s",
            {"id": quest_id, "now": clock.now()},
        )

    async def create(
        self, *, item: QuestItem, amount: int, diamonds: int, name: str
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO quests (item, amount, diamonds, name) "
            "VALUES (%(item)s, %(amount)s, %(diamonds)s, %(name)s)",
            {"item": int(item), "amount": amount, "diamonds": diamonds, "name": name},
        )

        return result.last_row_id


class UserQuestRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def list_current(self, user_id: int, expires_at: datetime) -> list[UserQuest]:
        rows = await self._mysql.fetch_all(
            "SELECT id, user_id, slot, quest_id, assigned_at, expires_at "
            "FROM user_quests WHERE user_id = %(id)s AND expires_at = %(expires)s "
            "ORDER BY slot",
            {"id": user_id, "expires": expires_at},
        )

        return [UserQuest.model_validate(row) for row in rows]

    async def assign(
        self, user_id: int, slot: int, quest_id: int, expires_at: datetime
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO user_quests (user_id, slot, quest_id, expires_at) "
            "VALUES (%(user)s, %(slot)s, %(quest)s, %(expires)s)",
            {"user": user_id, "slot": slot, "quest": quest_id, "expires": expires_at},
        )

        return result.last_row_id

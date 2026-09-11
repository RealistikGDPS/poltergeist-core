from datetime import datetime

from gdformat.enums import ChestType
from gdformat.enums import RewardItem

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.utilities import clock

_COLUMNS = "id, reward_key, chest_type, max_claims, expires_at"


class SecretReward(Model):
    id: int
    reward_key: str
    chest_type: ChestType
    max_claims: int | None
    expires_at: datetime | None


class SecretRewardItem(Model):
    item: RewardItem
    amount: int


class SecretRewardRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_key(self, reward_key: str) -> SecretReward | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM secret_rewards WHERE reward_key = %(key)s "
            "AND deleted_at IS NULL",
            {"key": reward_key},
        )

        return None if row is None else SecretReward.model_validate(row)

    async def list_items(self, reward_id: int) -> list[SecretRewardItem]:
        rows = await self._mysql.fetch_all(
            "SELECT item, amount FROM secret_reward_items "
            "WHERE secret_reward_id = %(id)s ORDER BY item",
            {"id": reward_id},
        )

        return [SecretRewardItem.model_validate(row) for row in rows]

    async def count_claims(self, reward_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM secret_reward_claims WHERE secret_reward_id = %(id)s",
            {"id": reward_id},
        )

        return count

    async def has_claimed(self, reward_id: int, user_id: int) -> bool:
        value = await self._mysql.fetch_val(
            "SELECT 1 FROM secret_reward_claims WHERE secret_reward_id = %(id)s "
            "AND user_id = %(user)s",
            {"id": reward_id, "user": user_id},
        )

        return value is not None

    async def create_claim(self, reward_id: int, user_id: int) -> None:
        await self._mysql.execute(
            "INSERT INTO secret_reward_claims (secret_reward_id, user_id) "
            "VALUES (%(id)s, %(user)s)",
            {"id": reward_id, "user": user_id},
        )

    async def create(
        self,
        *,
        reward_key: str,
        chest_type: ChestType,
        max_claims: int | None,
        expires_at: datetime | None,
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO secret_rewards (reward_key, chest_type, max_claims, "
            "expires_at) "
            "VALUES (%(key)s, %(type)s, %(max)s, %(expires)s)",
            {
                "key": reward_key,
                "type": int(chest_type),
                "max": max_claims,
                "expires": expires_at,
            },
        )

        return result.last_row_id

    async def list_all(self) -> list[SecretReward]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM secret_rewards WHERE deleted_at IS NULL ORDER BY "
            f"id DESC"
        )

        return [SecretReward.model_validate(row) for row in rows]

    async def soft_delete(self, reward_id: int) -> None:
        await self._mysql.execute(
            "UPDATE secret_rewards SET deleted_at = %(now)s WHERE id = %(id)s",
            {"id": reward_id, "now": clock.now()},
        )

    async def add_item(self, reward_id: int, item: RewardItem, amount: int) -> None:
        await self._mysql.execute(
            "INSERT INTO secret_reward_items (secret_reward_id, item, amount) "
            "VALUES (%(id)s, %(item)s, %(amount)s) "
            "ON DUPLICATE KEY UPDATE amount = VALUES(amount)",
            {"id": reward_id, "item": int(item), "amount": amount},
        )

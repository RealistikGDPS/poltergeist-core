from datetime import datetime

from gdformat.enums import ChestType
from gdformat.enums import Shard

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model

_COLUMNS = "id, user_id, chest_type, orbs, diamonds, shard, demon_keys, claimed_at"


class ChestClaim(Model):
    id: int
    user_id: int
    chest_type: ChestType
    orbs: int
    diamonds: int
    shard: Shard
    demon_keys: int
    claimed_at: datetime


class ChestRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_latest(
        self, user_id: int, chest_type: ChestType
    ) -> ChestClaim | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM chest_claims WHERE user_id = %(id)s "
            "AND chest_type = %(type)s ORDER BY claimed_at DESC LIMIT 1",
            {"id": user_id, "type": int(chest_type)},
        )

        return None if row is None else ChestClaim.model_validate(row)

    async def count(self, user_id: int, chest_type: ChestType) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM chest_claims WHERE user_id = %(id)s "
            "AND chest_type = %(type)s",
            {"id": user_id, "type": int(chest_type)},
        )

        return count

    async def create(
        self,
        user_id: int,
        chest_type: ChestType,
        *,
        orbs: int,
        diamonds: int,
        shard: Shard,
        demon_keys: int,
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO chest_claims (user_id, chest_type, orbs, diamonds, shard, "
            "demon_keys) VALUES (%(id)s, %(type)s, %(orbs)s, %(diamonds)s, %(shard)s, "
            "%(keys)s)",
            {
                "id": user_id,
                "type": int(chest_type),
                "orbs": orbs,
                "diamonds": diamonds,
                "shard": int(shard),
                "keys": demon_keys,
            },
        )

        return result.last_row_id

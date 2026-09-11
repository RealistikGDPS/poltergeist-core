from datetime import datetime

from gdformat.enums import Platform

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.utilities import clock


class Device(Model):
    user_id: int
    udid: str
    platform: Platform
    first_seen_at: datetime
    last_seen_at: datetime


class DeviceRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def upsert(self, user_id: int, udid: str, platform: Platform) -> None:
        await self._mysql.execute(
            "INSERT INTO user_devices (user_id, udid, platform) "
            "VALUES (%(id)s, %(udid)s, %(platform)s) ON DUPLICATE KEY UPDATE "
            "platform = VALUES(platform), last_seen_at = %(now)s",
            {
                "id": user_id,
                "udid": udid,
                "platform": int(platform),
                "now": clock.now(),
            },
        )

    async def list_by_user(self, user_id: int) -> list[Device]:
        rows = await self._mysql.fetch_all(
            "SELECT user_id, udid, platform, first_seen_at, last_seen_at "
            "FROM user_devices WHERE user_id = %(id)s ORDER BY last_seen_at DESC",
            {"id": user_id},
        )

        return [Device.model_validate(row) for row in rows]

    async def list_user_ids_by_udid(self, udid: str) -> list[int]:
        rows = await self._mysql.fetch_all(
            "SELECT user_id FROM user_devices WHERE udid = %(udid)s",
            {"udid": udid},
        )

        return [int(row["user_id"]) for row in rows]

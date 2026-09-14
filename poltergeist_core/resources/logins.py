import ipaddress
from datetime import datetime
from enum import StrEnum
from typing import Annotated
from typing import Any

from gdformat.enums import Platform
from pydantic import BeforeValidator

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset

_COLUMNS = (
    "id, user_id, source, ip, udid, platform, game_version, binary_version, created_at"
)


def _unpack_ip(value: Any) -> Any:
    if isinstance(value, bytes):
        return str(ipaddress.ip_address(value))

    return value


type PackedIp = Annotated[str, BeforeValidator(_unpack_ip)]


class LoginSource(StrEnum):
    GAME = "game"
    WEB = "web"


class UserLogin(Model):
    id: int
    user_id: int
    source: LoginSource
    ip: PackedIp
    udid: str
    platform: Platform
    game_version: int
    binary_version: int
    created_at: datetime


class LoginRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def create(
        self,
        user_id: int,
        source: LoginSource,
        *,
        ip: bytes,
        udid: str,
        platform: Platform,
        game_version: int,
        binary_version: int,
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO user_logins (user_id, source, ip, udid, platform, "
            "game_version, binary_version) VALUES (%(user)s, %(source)s, %(ip)s, "
            "%(udid)s, %(platform)s, %(game)s, %(binary)s)",
            {
                "user": user_id,
                "source": source.value,
                "ip": ip,
                "udid": udid,
                "platform": int(platform),
                "game": game_version,
                "binary": binary_version,
            },
        )

        return result.last_row_id

    async def list_by_user(self, user_id: int, page: int, size: int) -> list[UserLogin]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM user_logins WHERE user_id = %(id)s "
            "ORDER BY id DESC LIMIT %(limit)s OFFSET %(offset)s",
            {"id": user_id, "limit": size, "offset": offset(page, size)},
        )

        return [UserLogin.model_validate(row) for row in rows]

    async def count_by_user(self, user_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM user_logins WHERE user_id = %(id)s",
            {"id": user_id},
        )

        return count

    async def list_user_ids_sharing_ip(
        self, user_id: int, *, since: datetime
    ) -> list[int]:
        """Other users seen from an address this user logged in from."""

        rows = await self._mysql.fetch_all(
            "SELECT DISTINCT other.user_id FROM user_logins own "
            "JOIN user_logins other ON other.ip = own.ip "
            "WHERE own.user_id = %(id)s AND other.user_id <> %(id)s "
            "AND own.created_at >= %(since)s AND other.created_at >= %(since)s",
            {"id": user_id, "since": since},
        )

        return [int(row["user_id"]) for row in rows]

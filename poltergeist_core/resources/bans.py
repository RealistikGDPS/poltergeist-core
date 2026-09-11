from datetime import datetime
from enum import StrEnum

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset
from poltergeist_core.utilities import clock

_COLUMNS = (
    "id, user_id, type, reason, issued_by_user_id, created_at, expires_at, "
    "revoked_at, revoked_by_user_id"
)
_ACTIVE = "revoked_at IS NULL AND (expires_at IS NULL OR expires_at > %(now)s)"


class BanType(StrEnum):
    ACCOUNT = "account"
    COMMENT = "comment"
    UPLOAD = "upload"
    LEADERBOARD = "leaderboard"
    CREATOR = "creator"


class UserBan(Model):
    id: int
    user_id: int
    type: BanType
    reason: str
    issued_by_user_id: int | None
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    revoked_by_user_id: int | None

    @property
    def seconds_left(self) -> int | None:
        if self.expires_at is None:
            return None

        return clock.seconds_until(self.expires_at)


class BanRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_active(self, user_id: int, ban_type: BanType) -> UserBan | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM user_bans WHERE user_id = %(id)s "
            f"AND type = %(type)s AND {_ACTIVE} ORDER BY expires_at IS NULL DESC, "
            "expires_at DESC LIMIT 1",
            {"id": user_id, "type": ban_type.value, "now": clock.now()},
        )

        return None if row is None else UserBan.model_validate(row)

    async def list_active(self, user_id: int) -> list[UserBan]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM user_bans WHERE user_id = %(id)s AND {_ACTIVE} "
            "ORDER BY created_at DESC",
            {"id": user_id, "now": clock.now()},
        )

        return [UserBan.model_validate(row) for row in rows]

    async def list_all_active(self, page: int, size: int) -> list[UserBan]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM user_bans WHERE {_ACTIVE} "
            "ORDER BY created_at DESC LIMIT %(limit)s OFFSET %(offset)s",
            {"now": clock.now(), "limit": size, "offset": offset(page, size)},
        )

        return [UserBan.model_validate(row) for row in rows]

    async def count_all_active(self) -> int:
        count: int = await self._mysql.fetch_val(
            f"SELECT COUNT(*) FROM user_bans WHERE {_ACTIVE}", {"now": clock.now()}
        )

        return count

    async def create(
        self,
        user_id: int,
        ban_type: BanType,
        *,
        reason: str,
        issued_by_user_id: int | None,
        expires_at: datetime | None,
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO user_bans (user_id, type, reason, issued_by_user_id, "
            "expires_at) VALUES (%(id)s, %(type)s, %(reason)s, %(by)s, %(expires)s)",
            {
                "id": user_id,
                "type": ban_type.value,
                "reason": reason,
                "by": issued_by_user_id,
                "expires": expires_at,
            },
        )

        return result.last_row_id

    async def revoke_active(
        self, user_id: int, ban_type: BanType, *, revoked_by_user_id: int | None
    ) -> int:
        now = clock.now()

        result = await self._mysql.execute(
            "UPDATE user_bans SET revoked_at = %(now)s, revoked_by_user_id = %(by)s "
            f"WHERE user_id = %(id)s AND type = %(type)s AND {_ACTIVE}",
            {
                "id": user_id,
                "type": ban_type.value,
                "by": revoked_by_user_id,
                "now": now,
            },
        )

        return result.affected_rows

    async def list_banned_user_ids(self, ban_type: BanType) -> list[int]:
        rows = await self._mysql.fetch_all(
            f"SELECT DISTINCT user_id FROM user_bans WHERE type = %(type)s AND "
            f"{_ACTIVE}",
            {"type": ban_type.value, "now": clock.now()},
        )

        return [int(row["user_id"]) for row in rows]

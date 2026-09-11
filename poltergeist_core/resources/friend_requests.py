from datetime import datetime

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset
from poltergeist_core.resources._common import placeholders
from poltergeist_core.utilities import clock

_COLUMNS = "id, sender_user_id, recipient_user_id, message, created_at, read_at"


class FriendRequest(Model):
    id: int
    sender_user_id: int
    recipient_user_id: int
    message: str
    created_at: datetime
    read_at: datetime | None


class FriendRequestRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_id(self, request_id: int) -> FriendRequest | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM friend_requests WHERE id = %(id)s "
            "AND deleted_at IS NULL",
            {"id": request_id},
        )

        return None if row is None else FriendRequest.model_validate(row)

    async def find_by_pair(
        self, sender_user_id: int, recipient_user_id: int
    ) -> FriendRequest | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM friend_requests WHERE sender_user_id = %(sender)s "
            "AND recipient_user_id = %(recipient)s AND deleted_at IS NULL",
            {"sender": sender_user_id, "recipient": recipient_user_id},
        )

        return None if row is None else FriendRequest.model_validate(row)

    async def create(
        self, sender_user_id: int, recipient_user_id: int, message: str
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO friend_requests (sender_user_id, recipient_user_id, message, "
            "created_at) VALUES (%(sender)s, %(recipient)s, %(message)s, %(now)s) "
            "ON DUPLICATE KEY UPDATE message = VALUES(message), "
            "created_at = VALUES(created_at), read_at = NULL, deleted_at = NULL, "
            "id = LAST_INSERT_ID(id)",
            {
                "sender": sender_user_id,
                "recipient": recipient_user_id,
                "message": message,
                "now": clock.now(),
            },
        )

        return result.last_row_id

    async def mark_read(self, request_id: int) -> None:
        await self._mysql.execute(
            "UPDATE friend_requests SET read_at = %(now)s WHERE id = %(id)s "
            "AND read_at IS NULL",
            {"id": request_id, "now": clock.now()},
        )

    async def delete_incoming(
        self, recipient_user_id: int, sender_ids: list[int]
    ) -> None:
        if not sender_ids:
            return

        sql, values = placeholders(sender_ids, "sender")

        await self._mysql.execute(
            "UPDATE friend_requests SET deleted_at = %(now)s "
            f"WHERE recipient_user_id = %(recipient)s AND sender_user_id IN ({sql}) "
            "AND deleted_at IS NULL",
            {**values, "recipient": recipient_user_id, "now": clock.now()},
        )

    async def delete_outgoing(
        self, sender_user_id: int, recipient_ids: list[int]
    ) -> None:
        if not recipient_ids:
            return

        sql, values = placeholders(recipient_ids, "recipient")

        await self._mysql.execute(
            "UPDATE friend_requests SET deleted_at = %(now)s "
            f"WHERE sender_user_id = %(sender)s AND recipient_user_id IN ({sql}) "
            "AND deleted_at IS NULL",
            {**values, "sender": sender_user_id, "now": clock.now()},
        )

    async def delete_between(self, user_id: int, other_user_id: int) -> None:
        await self._mysql.execute(
            "UPDATE friend_requests SET deleted_at = %(now)s WHERE deleted_at IS NULL "
            "AND ((sender_user_id = %(a)s AND recipient_user_id = %(b)s) "
            "OR (sender_user_id = %(b)s AND recipient_user_id = %(a)s))",
            {"a": user_id, "b": other_user_id, "now": clock.now()},
        )

    async def list_incoming(
        self, recipient_user_id: int, page: int, size: int
    ) -> list[FriendRequest]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM friend_requests WHERE recipient_user_id = %(id)s "
            "AND deleted_at IS NULL ORDER BY created_at DESC "
            "LIMIT %(limit)s OFFSET %(offset)s",
            {"id": recipient_user_id, "limit": size, "offset": offset(page, size)},
        )

        return [FriendRequest.model_validate(row) for row in rows]

    async def list_outgoing(
        self, sender_user_id: int, page: int, size: int
    ) -> list[FriendRequest]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM friend_requests WHERE sender_user_id = %(id)s "
            "AND deleted_at IS NULL ORDER BY created_at DESC "
            "LIMIT %(limit)s OFFSET %(offset)s",
            {"id": sender_user_id, "limit": size, "offset": offset(page, size)},
        )

        return [FriendRequest.model_validate(row) for row in rows]

    async def count_incoming(self, recipient_user_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM friend_requests WHERE recipient_user_id = %(id)s "
            "AND deleted_at IS NULL",
            {"id": recipient_user_id},
        )

        return count

    async def count_outgoing(self, sender_user_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM friend_requests WHERE sender_user_id = %(id)s "
            "AND deleted_at IS NULL",
            {"id": sender_user_id},
        )

        return count

    async def count_unread_incoming(self, recipient_user_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM friend_requests WHERE recipient_user_id = %(id)s "
            "AND read_at IS NULL AND deleted_at IS NULL",
            {"id": recipient_user_id},
        )

        return count

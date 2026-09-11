from datetime import datetime

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset
from poltergeist_core.resources._common import placeholders
from poltergeist_core.utilities import clock

_COLUMNS = (
    "id, sender_user_id, recipient_user_id, subject, body, created_at, read_at, "
    "sender_deleted_at, recipient_deleted_at"
)


class Message(Model):
    id: int
    sender_user_id: int
    recipient_user_id: int
    subject: str
    body: str
    created_at: datetime
    read_at: datetime | None
    sender_deleted_at: datetime | None
    recipient_deleted_at: datetime | None


class MessageRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_id(self, message_id: int) -> Message | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM messages WHERE id = %(id)s",
            {"id": message_id},
        )

        return None if row is None else Message.model_validate(row)

    async def create(
        self, sender_user_id: int, recipient_user_id: int, subject: str, body: str
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO messages (sender_user_id, recipient_user_id, subject, body) "
            "VALUES (%(sender)s, %(recipient)s, %(subject)s, %(body)s)",
            {
                "sender": sender_user_id,
                "recipient": recipient_user_id,
                "subject": subject,
                "body": body,
            },
        )

        return result.last_row_id

    async def list_inbox(self, user_id: int, page: int, size: int) -> list[Message]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM messages WHERE recipient_user_id = %(id)s "
            "AND recipient_deleted_at IS NULL ORDER BY created_at DESC "
            "LIMIT %(limit)s OFFSET %(offset)s",
            {"id": user_id, "limit": size, "offset": offset(page, size)},
        )

        return [Message.model_validate(row) for row in rows]

    async def list_outbox(self, user_id: int, page: int, size: int) -> list[Message]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM messages WHERE sender_user_id = %(id)s "
            "AND sender_deleted_at IS NULL ORDER BY created_at DESC "
            "LIMIT %(limit)s OFFSET %(offset)s",
            {"id": user_id, "limit": size, "offset": offset(page, size)},
        )

        return [Message.model_validate(row) for row in rows]

    async def count_inbox(self, user_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM messages WHERE recipient_user_id = %(id)s "
            "AND recipient_deleted_at IS NULL",
            {"id": user_id},
        )

        return count

    async def count_outbox(self, user_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM messages WHERE sender_user_id = %(id)s "
            "AND sender_deleted_at IS NULL",
            {"id": user_id},
        )

        return count

    async def count_unread(self, user_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM messages WHERE recipient_user_id = %(id)s "
            "AND read_at IS NULL AND recipient_deleted_at IS NULL",
            {"id": user_id},
        )

        return count

    async def mark_read(self, message_id: int) -> None:
        await self._mysql.execute(
            "UPDATE messages SET read_at = %(now)s WHERE id = %(id)s AND read_at IS "
            "NULL",
            {"id": message_id, "now": clock.now()},
        )

    async def delete_for_recipient(self, user_id: int, message_ids: list[int]) -> None:
        if not message_ids:
            return

        sql, values = placeholders(message_ids, "message")

        await self._mysql.execute(
            "UPDATE messages SET recipient_deleted_at = %(now)s "
            f"WHERE recipient_user_id = %(id)s AND id IN ({sql})",
            {**values, "id": user_id, "now": clock.now()},
        )

    async def delete_for_sender(self, user_id: int, message_ids: list[int]) -> None:
        if not message_ids:
            return

        sql, values = placeholders(message_ids, "message")

        await self._mysql.execute(
            "UPDATE messages SET sender_deleted_at = %(now)s "
            f"WHERE sender_user_id = %(id)s AND id IN ({sql})",
            {**values, "id": user_id, "now": clock.now()},
        )

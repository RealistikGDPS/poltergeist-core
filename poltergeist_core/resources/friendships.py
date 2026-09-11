from datetime import datetime

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.utilities import clock


class Friendship(Model):
    user_id: int
    friend_user_id: int
    created_at: datetime
    seen_at: datetime | None


class FriendshipRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def are_friends(self, user_id: int, other_user_id: int) -> bool:
        value = await self._mysql.fetch_val(
            "SELECT 1 FROM friendships WHERE user_id = %(id)s "
            "AND friend_user_id = %(other)s AND deleted_at IS NULL",
            {"id": user_id, "other": other_user_id},
        )

        return value is not None

    async def list_by_user(self, user_id: int) -> list[Friendship]:
        rows = await self._mysql.fetch_all(
            "SELECT user_id, friend_user_id, created_at, seen_at FROM friendships "
            "WHERE user_id = %(id)s AND deleted_at IS NULL ORDER BY created_at DESC",
            {"id": user_id},
        )

        return [Friendship.model_validate(row) for row in rows]

    async def list_friend_ids(self, user_id: int) -> list[int]:
        rows = await self._mysql.fetch_all(
            "SELECT friend_user_id FROM friendships WHERE user_id = %(id)s "
            "AND deleted_at IS NULL",
            {"id": user_id},
        )

        return [int(row["friend_user_id"]) for row in rows]

    async def count_unseen(self, user_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM friendships WHERE user_id = %(id)s "
            "AND seen_at IS NULL AND deleted_at IS NULL",
            {"id": user_id},
        )

        return count

    async def mark_seen(self, user_id: int) -> None:
        await self._mysql.execute(
            "UPDATE friendships SET seen_at = %(now)s WHERE user_id = %(id)s "
            "AND seen_at IS NULL AND deleted_at IS NULL",
            {"id": user_id, "now": clock.now()},
        )

    async def create_pair(self, accepter_user_id: int, sender_user_id: int) -> None:
        """Writes both directions. The accepter already knows about the
        friendship, so only the sender's row starts unseen."""

        now = clock.now()

        await self._mysql.execute(
            "INSERT INTO friendships (user_id, friend_user_id, created_at, seen_at) "
            "VALUES (%(accepter)s, %(sender)s, %(now)s, %(now)s), "
            "(%(sender)s, %(accepter)s, %(now)s, NULL) ON DUPLICATE KEY UPDATE "
            "created_at = VALUES(created_at), seen_at = VALUES(seen_at), "
            "deleted_at = NULL",
            {"accepter": accepter_user_id, "sender": sender_user_id, "now": now},
        )

    async def delete_pair(self, user_id: int, other_user_id: int) -> None:
        await self._mysql.execute(
            "UPDATE friendships SET deleted_at = %(now)s WHERE deleted_at IS NULL AND "
            "((user_id = %(a)s AND friend_user_id = %(b)s) OR "
            "(user_id = %(b)s AND friend_user_id = %(a)s))",
            {"a": user_id, "b": other_user_id, "now": clock.now()},
        )

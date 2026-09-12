from datetime import datetime
from enum import StrEnum

from gdformat.enums import CommentHistoryState
from gdformat.enums import FriendRequestState
from gdformat.enums import MessageState

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset
from poltergeist_core.resources._common import placeholders
from poltergeist_core.utilities import clock

_COLUMNS = (
    "id, username, kind, email, comment_colour, message_privacy, "
    "friend_request_privacy, comment_history_privacy, youtube, twitter, twitch, "
    "discord, instagram, tiktok, custom, registered_at, last_seen_at, deleted_at"
)


class UserKind(StrEnum):
    """Identity, not capability: a non-player account never ranks and its
    levels stay out of the curated listings. What it may do is still decided
    by its roles."""

    PLAYER = "player"
    BOT = "bot"


class User(Model):
    id: int
    username: str
    kind: UserKind
    email: str
    comment_colour: int | None
    message_privacy: MessageState
    friend_request_privacy: FriendRequestState
    comment_history_privacy: CommentHistoryState
    youtube: str
    twitter: str
    twitch: str
    discord: str
    instagram: str
    tiktok: str
    custom: str
    registered_at: datetime
    last_seen_at: datetime | None
    deleted_at: datetime | None


class UserRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_id(self, user_id: int) -> User | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM users WHERE id = %(id)s AND deleted_at IS NULL",
            {"id": user_id},
        )

        return None if row is None else User.model_validate(row)

    async def find_by_username(self, username: str) -> User | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM users WHERE username = %(username)s "
            "AND deleted_at IS NULL",
            {"username": username},
        )

        return None if row is None else User.model_validate(row)

    async def find_by_email(self, email: str) -> User | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM users WHERE email = %(email)s "
            "AND deleted_at IS NULL",
            {"email": email},
        )

        return None if row is None else User.model_validate(row)

    async def find_many_by_ids(self, user_ids: list[int]) -> list[User]:
        if not user_ids:
            return []

        sql, values = placeholders(user_ids, "id")

        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM users WHERE id IN ({sql}) AND deleted_at IS NULL",
            values,
        )

        return [User.model_validate(row) for row in rows]

    async def create(self, username: str, email: str) -> int:
        result = await self._mysql.execute(
            "INSERT INTO users (username, email) VALUES (%(username)s, %(email)s)",
            {"username": username, "email": email},
        )

        return result.last_row_id

    async def update_settings(
        self,
        user_id: int,
        *,
        message_privacy: MessageState,
        friend_request_privacy: FriendRequestState,
        comment_history_privacy: CommentHistoryState,
        youtube: str,
        twitter: str,
        twitch: str,
        discord: str,
        instagram: str,
        tiktok: str,
        custom: str,
    ) -> None:
        await self._mysql.execute(
            "UPDATE users SET message_privacy = %(message_privacy)s, "
            "friend_request_privacy = %(friend_request_privacy)s, "
            "comment_history_privacy = %(comment_history_privacy)s, "
            "youtube = %(youtube)s, twitter = %(twitter)s, twitch = %(twitch)s, "
            "discord = %(discord)s, instagram = %(instagram)s, tiktok = %(tiktok)s, "
            "custom = %(custom)s WHERE id = %(id)s",
            {
                "id": user_id,
                "message_privacy": int(message_privacy),
                "friend_request_privacy": int(friend_request_privacy),
                "comment_history_privacy": int(comment_history_privacy),
                "youtube": youtube,
                "twitter": twitter,
                "twitch": twitch,
                "discord": discord,
                "instagram": instagram,
                "tiktok": tiktok,
                "custom": custom,
            },
        )

    async def update_comment_colour(self, user_id: int, colour: int | None) -> None:
        await self._mysql.execute(
            "UPDATE users SET comment_colour = %(colour)s WHERE id = %(id)s",
            {"id": user_id, "colour": colour},
        )

    async def update_username(self, user_id: int, username: str) -> None:
        await self._mysql.execute(
            "UPDATE users SET username = %(username)s WHERE id = %(id)s",
            {"id": user_id, "username": username},
        )

    async def update_kind(self, user_id: int, kind: UserKind) -> None:
        await self._mysql.execute(
            "UPDATE users SET kind = %(kind)s WHERE id = %(id)s",
            {"id": user_id, "kind": kind.value},
        )

    async def list_non_player_ids(self) -> list[int]:
        """Includes soft-deleted accounts so they stay out of the rankings."""

        rows = await self._mysql.fetch_all(
            "SELECT id FROM users WHERE kind <> %(player)s",
            {"player": UserKind.PLAYER.value},
        )

        return [int(row["id"]) for row in rows]

    async def touch_last_seen(self, user_id: int) -> None:
        await self._mysql.execute(
            "UPDATE users SET last_seen_at = %(now)s WHERE id = %(id)s",
            {"id": user_id, "now": clock.now()},
        )

    async def search(self, prefix: str, page: int, size: int) -> list[User]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM users WHERE username LIKE %(pattern)s "
            "AND deleted_at IS NULL ORDER BY username LIMIT %(limit)s OFFSET "
            "%(offset)s",
            {"pattern": f"{prefix}%", "limit": size, "offset": offset(page, size)},
        )

        return [User.model_validate(row) for row in rows]

    async def list_page(
        self, *, query: str, order: str, page: int, size: int
    ) -> list[User]:
        """`order` is one of `newest`, `oldest`, `recently_seen`, `name`."""

        ordering = {
            "newest": "id DESC",
            "oldest": "id ASC",
            "recently_seen": "last_seen_at DESC, id DESC",
            "name": "username ASC",
        }[order]

        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM users WHERE deleted_at IS NULL "
            "AND (username LIKE %(pattern)s OR email LIKE %(pattern)s "
            "OR id = %(exact)s) "
            f"ORDER BY {ordering} LIMIT %(limit)s OFFSET %(offset)s",
            {
                "pattern": f"%{query}%",
                "exact": int(query) if query.isdecimal() else 0,
                "limit": size,
                "offset": offset(page, size),
            },
        )

        return [User.model_validate(row) for row in rows]

    async def count_page(self, *, query: str) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM users WHERE deleted_at IS NULL "
            "AND (username LIKE %(pattern)s OR email LIKE %(pattern)s "
            "OR id = %(exact)s)",
            {
                "pattern": f"%{query}%",
                "exact": int(query) if query.isdecimal() else 0,
            },
        )

        return count

    async def count_search(self, prefix: str) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM users WHERE username LIKE %(pattern)s "
            "AND deleted_at IS NULL",
            {"pattern": f"{prefix}%"},
        )

        return count

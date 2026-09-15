import json
from enum import StrEnum

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.adapters.redis import RedisClient
from poltergeist_core.utilities import clock
from poltergeist_core.utilities import permissions
from poltergeist_core.utilities.permissions import DENY_PREFIX

_CACHE_SECONDS = 300


class Permission(StrEnum):
    """The permissions the server itself checks. Roles may hold any dotted
    string, including wildcards such as `levels.*`."""

    LEVELS_UPLOAD = "levels.upload"
    LEVELS_REPORT = "levels.report"
    LEVELS_EDIT_ANY = "levels.edit_any"
    LEVELS_MOVE = "levels.move"
    LEVELS_DELETE_ANY = "levels.delete_any"
    LEVELS_RATE = "levels.rate"
    LEVELS_FEATURE = "levels.feature"
    LEVELS_RATE_DEMON = "levels.rate_demon"
    LEVELS_SUGGEST = "levels.suggest"
    LEVELS_VIEW_SUGGESTIONS = "levels.view_suggestions"
    LEVELS_VIEW_REPORTS = "levels.view_reports"
    LISTS_UPLOAD = "lists.upload"
    LISTS_EDIT_ANY = "lists.edit_any"
    LISTS_DELETE_ANY = "lists.delete_any"
    LISTS_RATE = "lists.rate"
    COMMENTS_POST = "comments.post"
    COMMENTS_DELETE_ANY = "comments.delete_any"
    COMMENTS_COLOUR = "comments.colour"
    PROFILE_POST = "profile.post"
    PROFILE_DELETE_ANY = "profile.delete_any"
    MESSAGES_SEND = "messages.send"
    FRIENDS_REQUEST = "friends.request"
    USERS_BLOCK = "users.block"
    USERS_BAN_ACCOUNT = "users.ban.account"
    USERS_BAN_COMMENT = "users.ban.comment"
    USERS_BAN_UPLOAD = "users.ban.upload"
    USERS_BAN_LEADERBOARD = "users.ban.leaderboard"
    USERS_BAN_CREATOR = "users.ban.creator"
    USERS_UNBAN = "users.unban"
    USERS_ROLES_ASSIGN = "users.roles.assign"
    USERS_ROLES_REVOKE = "users.roles.revoke"
    USERS_KIND_MANAGE = "users.kind.manage"
    USERS_EDIT_ANY = "users.edit_any"
    SCORES_SUBMIT = "scores.submit"
    STATS_UPDATE = "stats.update"
    STATS_RESTORE = "stats.restore"
    FLAGS_REVIEW = "flags.review"
    LEADERBOARD_RANK = "leaderboard.rank"
    REWARDS_CLAIM = "rewards.claim"
    QUESTS_VIEW = "quests.view"
    SAVES_BACKUP = "saves.backup"
    SONGS_REQUEST = "songs.request"
    SONGS_MANAGE = "songs.manage"
    TIMELY_SCHEDULE = "timely.schedule"
    PACKS_MANAGE = "packs.manage"
    MOD_BADGE_MODERATOR = "mod.badge.moderator"
    MOD_BADGE_ELDER = "mod.badge.elder"
    MOD_BADGE_LEADERBOARD = "mod.badge.leaderboard"
    COMMANDS_USE = "commands.use"
    ADMIN_ACCESS = "admin.access"
    ADMIN_SETTINGS = "admin.settings"
    ADMIN_MAINTENANCE = "admin.maintenance"


class PermissionRepository:
    """Resolves a user's effective grants from roles and overrides, cached in
    Redis. Every change to roles or overrides MUST call `invalidate`."""

    __slots__ = ("_mysql", "_redis")

    def __init__(self, mysql: ImplementsMySQL, redis: RedisClient) -> None:
        self._mysql = mysql
        self._redis = redis

    @staticmethod
    def _key(user_id: int) -> str:
        return f"permissions:{user_id}"

    async def _load(self, user_id: int) -> frozenset[str]:
        now = clock.now()

        role_rows = await self._mysql.fetch_all(
            "SELECT rp.permission FROM role_permissions rp "
            "JOIN user_roles ur ON ur.role_id = rp.role_id "
            "JOIN roles r ON r.id = rp.role_id WHERE ur.user_id = %(id)s "
            "AND ur.deleted_at IS NULL AND r.deleted_at IS NULL "
            "AND (ur.expires_at IS NULL OR ur.expires_at > %(now)s)",
            {"id": user_id, "now": now},
        )
        override_rows = await self._mysql.fetch_all(
            "SELECT permission, effect FROM user_permissions WHERE user_id = %(id)s "
            "AND deleted_at IS NULL AND (expires_at IS NULL OR expires_at > %(now)s)",
            {"id": user_id, "now": now},
        )
        granted = {str(row["permission"]) for row in role_rows}

        for row in override_rows:
            permission = str(row["permission"])

            if row["effect"] == "deny":
                granted.add(DENY_PREFIX + permission)
            else:
                granted.add(permission)

        return frozenset(granted)

    async def effective(self, user_id: int) -> frozenset[str]:
        cached = await self._redis.get(self._key(user_id))

        if cached is not None:
            return frozenset(json.loads(cached))

        granted = await self._load(user_id)

        await self._redis.set(
            self._key(user_id), json.dumps(sorted(granted)), ex=_CACHE_SECONDS
        )

        return granted

    async def effective_many(self, user_ids: list[int]) -> dict[int, frozenset[str]]:
        if not user_ids:
            return {}

        unique = list(dict.fromkeys(user_ids))
        cached = await self._redis.mget([self._key(user_id) for user_id in unique])
        result: dict[int, frozenset[str]] = {}

        for user_id, value in zip(unique, cached, strict=True):
            if value is None:
                result[user_id] = await self.effective(user_id)
            else:
                result[user_id] = frozenset(json.loads(value))

        return result

    async def has(self, user_id: int, permission: str) -> bool:
        return permissions.is_granted(await self.effective(user_id), permission)

    async def invalidate(self, user_id: int) -> None:
        await self._redis.delete(self._key(user_id))

    async def set_override(
        self,
        user_id: int,
        permission: str,
        *,
        allow: bool,
        granted_by_user_id: int | None,
    ) -> None:
        await self._mysql.execute(
            "INSERT INTO user_permissions (user_id, permission, effect, "
            "granted_by_user_id, created_at) VALUES (%(user)s, %(permission)s, "
            "%(effect)s, %(by)s, %(now)s) ON DUPLICATE KEY UPDATE "
            "effect = VALUES(effect), granted_by_user_id = VALUES(granted_by_user_id), "
            "created_at = VALUES(created_at), deleted_at = NULL",
            {
                "user": user_id,
                "permission": permission,
                "effect": "allow" if allow else "deny",
                "by": granted_by_user_id,
                "now": clock.now(),
            },
        )

    async def clear_override(self, user_id: int, permission: str) -> None:
        await self._mysql.execute(
            "UPDATE user_permissions SET deleted_at = %(now)s WHERE user_id = %(user)s "
            "AND permission = %(permission)s AND deleted_at IS NULL",
            {"user": user_id, "permission": permission, "now": clock.now()},
        )

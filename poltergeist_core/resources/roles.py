from datetime import datetime

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.utilities import clock

_COLUMNS = "id, name, description, priority"


class Role(Model):
    id: int
    name: str
    description: str
    priority: int


class RoleRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_id(self, role_id: int) -> Role | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM roles WHERE id = %(id)s AND deleted_at IS NULL",
            {"id": role_id},
        )

        return None if row is None else Role.model_validate(row)

    async def find_by_name(self, name: str) -> Role | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM roles WHERE name = %(name)s AND deleted_at IS "
            f"NULL",
            {"name": name},
        )

        return None if row is None else Role.model_validate(row)

    async def list_all(self) -> list[Role]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM roles WHERE deleted_at IS NULL ORDER BY priority "
            f"DESC"
        )

        return [Role.model_validate(row) for row in rows]

    async def list_by_user(self, user_id: int) -> list[Role]:
        rows = await self._mysql.fetch_all(
            "SELECT r.id, r.name, r.description, r.priority FROM roles r "
            "JOIN user_roles ur ON ur.role_id = r.id WHERE ur.user_id = %(id)s "
            "AND ur.deleted_at IS NULL AND r.deleted_at IS NULL "
            "AND (ur.expires_at IS NULL OR ur.expires_at > %(now)s) "
            "ORDER BY r.priority DESC",
            {"id": user_id, "now": clock.now()},
        )

        return [Role.model_validate(row) for row in rows]

    async def list_permissions(self, role_id: int) -> list[str]:
        rows = await self._mysql.fetch_all(
            "SELECT permission FROM role_permissions WHERE role_id = %(id)s "
            "ORDER BY permission",
            {"id": role_id},
        )

        return [str(row["permission"]) for row in rows]

    async def create(self, name: str, description: str, priority: int) -> int:
        result = await self._mysql.execute(
            "INSERT INTO roles (name, description, priority) "
            "VALUES (%(name)s, %(description)s, %(priority)s)",
            {"name": name, "description": description, "priority": priority},
        )

        return result.last_row_id

    async def update(
        self, role_id: int, *, name: str, description: str, priority: int
    ) -> None:
        await self._mysql.execute(
            "UPDATE roles SET name = %(name)s, description = %(description)s, "
            "priority = %(priority)s WHERE id = %(id)s",
            {
                "id": role_id,
                "name": name,
                "description": description,
                "priority": priority,
            },
        )

    async def soft_delete(self, role_id: int) -> None:
        await self._mysql.execute(
            "UPDATE roles SET deleted_at = %(now)s WHERE id = %(id)s",
            {"id": role_id, "now": clock.now()},
        )

    async def count_members(self, role_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM user_roles WHERE role_id = %(id)s AND deleted_at IS "
            "NULL "
            "AND (expires_at IS NULL OR expires_at > %(now)s)",
            {"id": role_id, "now": clock.now()},
        )

        return count

    async def list_member_ids(self, role_id: int) -> list[int]:
        rows = await self._mysql.fetch_all(
            "SELECT user_id FROM user_roles WHERE role_id = %(id)s AND deleted_at IS "
            "NULL "
            "AND (expires_at IS NULL OR expires_at > %(now)s) ORDER BY created_at DESC",
            {"id": role_id, "now": clock.now()},
        )

        return [int(row["user_id"]) for row in rows]

    async def replace_permissions(self, role_id: int, permissions: list[str]) -> None:
        await self._mysql.execute(
            "DELETE FROM role_permissions WHERE role_id = %(id)s",
            {"id": role_id},
        )

        for permission in permissions:
            await self._mysql.execute(
                "INSERT INTO role_permissions (role_id, permission) "
                "VALUES (%(id)s, %(permission)s)",
                {"id": role_id, "permission": permission},
            )

    async def assign(
        self,
        user_id: int,
        role_id: int,
        *,
        granted_by_user_id: int | None,
        expires_at: datetime | None,
    ) -> None:
        await self._mysql.execute(
            "INSERT INTO user_roles (user_id, role_id, granted_by_user_id, created_at, "
            "expires_at) VALUES (%(user)s, %(role)s, %(by)s, %(now)s, %(expires)s) "
            "ON DUPLICATE KEY UPDATE granted_by_user_id = VALUES(granted_by_user_id), "
            "created_at = VALUES(created_at), expires_at = VALUES(expires_at), "
            "deleted_at = NULL",
            {
                "user": user_id,
                "role": role_id,
                "by": granted_by_user_id,
                "now": clock.now(),
                "expires": expires_at,
            },
        )

    async def revoke(self, user_id: int, role_id: int) -> bool:
        result = await self._mysql.execute(
            "UPDATE user_roles SET deleted_at = %(now)s WHERE user_id = %(user)s "
            "AND role_id = %(role)s AND deleted_at IS NULL",
            {"user": user_id, "role": role_id, "now": clock.now()},
        )

        return result.affected_rows > 0

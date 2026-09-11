from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model


class UserCredential(Model):
    """`gjp2_bcrypt` is what a 2.2 client can be checked against. Accounts
    imported from the 2.1 server may only hold `legacy_password_bcrypt`, which
    needs the plaintext password and so cannot be used by the game client."""

    user_id: int
    gjp2_bcrypt: str | None
    legacy_password_bcrypt: str | None


class CredentialRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_user_id(self, user_id: int) -> UserCredential | None:
        row = await self._mysql.fetch_one(
            "SELECT user_id, gjp2_bcrypt, legacy_password_bcrypt FROM user_credentials "
            "WHERE user_id = %(id)s",
            {"id": user_id},
        )

        return None if row is None else UserCredential.model_validate(row)

    async def upsert(self, user_id: int, gjp2_bcrypt: str) -> None:
        """Setting a gjp2 hash retires any legacy plaintext hash."""

        await self._mysql.execute(
            "INSERT INTO user_credentials (user_id, gjp2_bcrypt) "
            "VALUES (%(id)s, %(hash)s) "
            "ON DUPLICATE KEY UPDATE gjp2_bcrypt = VALUES(gjp2_bcrypt), "
            "legacy_password_bcrypt = NULL",
            {"id": user_id, "hash": gjp2_bcrypt},
        )

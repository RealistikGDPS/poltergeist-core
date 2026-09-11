from abc import ABC
from abc import abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any
from typing import override

import asyncmy
from asyncmy.connection import Connection
from asyncmy.cursors import DictCursor
from asyncmy.pool import Pool

from poltergeist_core.utilities import logging

type MySQLValue = Any
type MySQLRow = Mapping[str, MySQLValue]
type MySQLValues = Mapping[str, MySQLValue]

logger = logging.get_logger(__name__)

_INIT_COMMAND = "SET time_zone = '+00:00'"


@dataclass(frozen=True, slots=True)
class ExecuteResult:
    last_row_id: int
    affected_rows: int


class ImplementsMySQL(ABC):
    """Queries are written with `%(name)s` placeholders and a mapping of values."""

    @abstractmethod
    async def fetch_one(
        self, query: str, values: MySQLValues | None = None
    ) -> MySQLRow | None: ...

    @abstractmethod
    async def fetch_all(
        self, query: str, values: MySQLValues | None = None
    ) -> list[MySQLRow]: ...

    @abstractmethod
    async def fetch_val(
        self, query: str, values: MySQLValues | None = None
    ) -> MySQLValue: ...

    @abstractmethod
    async def execute(
        self, query: str, values: MySQLValues | None = None
    ) -> ExecuteResult: ...


async def _fetch_one(
    connection: Connection, query: str, values: MySQLValues | None
) -> MySQLRow | None:
    async with connection.cursor(DictCursor) as cursor:
        await cursor.execute(query, values)
        row: MySQLRow | None = await cursor.fetchone()

        return row


async def _fetch_all(
    connection: Connection, query: str, values: MySQLValues | None
) -> list[MySQLRow]:
    async with connection.cursor(DictCursor) as cursor:
        await cursor.execute(query, values)
        rows: list[MySQLRow] = list(await cursor.fetchall())

        return rows


async def _fetch_val(
    connection: Connection, query: str, values: MySQLValues | None
) -> MySQLValue:
    async with connection.cursor() as cursor:
        await cursor.execute(query, values)
        row: tuple[MySQLValue, ...] | None = await cursor.fetchone()

        if row is None:
            return None

        return row[0]


async def _execute(
    connection: Connection, query: str, values: MySQLValues | None
) -> ExecuteResult:
    async with connection.cursor() as cursor:
        affected: int = await cursor.execute(query, values)

        return ExecuteResult(last_row_id=cursor.lastrowid, affected_rows=affected)


class MySQLPool(ImplementsMySQL):
    """A pool of autocommitting connections. Reads go straight through it;
    writes that must be atomic go through `transaction()`."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        pool_min: int,
        pool_max: int,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._database = database
        self._pool_min = pool_min
        self._pool_max = pool_max
        self._pool: Pool | None = None

    @property
    def _connected_pool(self) -> Pool:
        assert self._pool is not None, "MySQL pool used before connect()."

        return self._pool

    async def connect(self) -> None:
        self._pool = await asyncmy.create_pool(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            db=self._database,
            minsize=self._pool_min,
            maxsize=self._pool_max,
            autocommit=True,
            charset="utf8mb4",
            init_command=_INIT_COMMAND,
        )

    async def disconnect(self) -> None:
        pool = self._connected_pool
        pool.close()
        await pool.wait_closed()
        self._pool = None

    def transaction(self) -> MySQLTransaction:
        return MySQLTransaction(self._connected_pool)

    @override
    async def fetch_one(
        self, query: str, values: MySQLValues | None = None
    ) -> MySQLRow | None:
        async with self._connected_pool.acquire() as connection:
            return await _fetch_one(connection, query, values)

    @override
    async def fetch_all(
        self, query: str, values: MySQLValues | None = None
    ) -> list[MySQLRow]:
        async with self._connected_pool.acquire() as connection:
            return await _fetch_all(connection, query, values)

    @override
    async def fetch_val(
        self, query: str, values: MySQLValues | None = None
    ) -> MySQLValue:
        async with self._connected_pool.acquire() as connection:
            return await _fetch_val(connection, query, values)

    @override
    async def execute(
        self, query: str, values: MySQLValues | None = None
    ) -> ExecuteResult:
        async with self._connected_pool.acquire() as connection:
            return await _execute(connection, query, values)


class MySQLTransaction(ImplementsMySQL):
    """A single pooled connection inside `BEGIN`/`COMMIT`; rolls back when the
    block exits with an exception."""

    __slots__ = ("_connection", "_pool")  # Built for every writing request.

    def __init__(self, pool: Pool) -> None:
        self._pool = pool
        self._connection: Connection | None = None

    @property
    def _active(self) -> Connection:
        assert self._connection is not None, "Transaction used outside its block."

        return self._connection

    async def __aenter__(self) -> MySQLTransaction:
        self._connection = await self._pool.acquire()
        await self._connection.begin()

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        connection = self._active

        try:
            if exc_type is None:
                await connection.commit()
            else:
                await connection.rollback()
        finally:
            await self._pool.release(connection)
            self._connection = None

    @override
    async def fetch_one(
        self, query: str, values: MySQLValues | None = None
    ) -> MySQLRow | None:
        return await _fetch_one(self._active, query, values)

    @override
    async def fetch_all(
        self, query: str, values: MySQLValues | None = None
    ) -> list[MySQLRow]:
        return await _fetch_all(self._active, query, values)

    @override
    async def fetch_val(
        self, query: str, values: MySQLValues | None = None
    ) -> MySQLValue:
        return await _fetch_val(self._active, query, values)

    @override
    async def execute(
        self, query: str, values: MySQLValues | None = None
    ) -> ExecuteResult:
        return await _execute(self._active, query, values)


def default() -> MySQLPool:
    """Builds the production pool; `connect()` still has to be awaited."""

    # Local import keeps this module importable without configuration.
    from poltergeist_core import settings

    return MySQLPool(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_TCP_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        database=settings.MYSQL_DATABASE,
        pool_min=settings.MYSQL_POOL_MIN,
        pool_max=settings.MYSQL_POOL_MAX,
    )

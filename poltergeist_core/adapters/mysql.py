from abc import ABC
from abc import abstractmethod
from collections.abc import Mapping
from types import TracebackType
from typing import Any
from typing import override

import beerstream1
from beerstream1 import ExecuteResult

from poltergeist_core.utilities import logging

type MySQLValue = Any
type MySQLRow = Mapping[str, MySQLValue]
type MySQLValues = Mapping[str, MySQLValue]

logger = logging.get_logger(__name__)

_INIT_COMMAND = "SET time_zone = '+00:00'"


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


class MySQLPool(ImplementsMySQL):
    """A pool of autocommitting connections. Reads go straight through it;
    writes that must be atomic go through `transaction()`."""

    __slots__ = ("_pool",)

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
        self._pool = beerstream1.Pool(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            min_size=pool_min,
            max_size=pool_max,
            autocommit=True,
            init_command=_INIT_COMMAND,
        )

    async def connect(self) -> None:
        await self._pool.connect()

    async def disconnect(self) -> None:
        await self._pool.close()

    def transaction(self) -> MySQLTransaction:
        return MySQLTransaction(self._pool)

    @override
    async def fetch_one(
        self, query: str, values: MySQLValues | None = None
    ) -> MySQLRow | None:
        return await self._pool.fetch_one(query, values)

    @override
    async def fetch_all(
        self, query: str, values: MySQLValues | None = None
    ) -> list[MySQLRow]:
        return list(await self._pool.fetch_all(query, values))

    @override
    async def fetch_val(
        self, query: str, values: MySQLValues | None = None
    ) -> MySQLValue:
        return await self._pool.fetch_val(query, values)

    @override
    async def execute(
        self, query: str, values: MySQLValues | None = None
    ) -> ExecuteResult:
        return await self._pool.execute(query, values)


class MySQLTransaction(ImplementsMySQL):
    """A single pooled connection inside `BEGIN`/`COMMIT`; rolls back when the
    block exits with an exception."""

    __slots__ = ("_pool", "_transaction")  # Built for every writing request.

    def __init__(self, pool: beerstream1.Pool) -> None:
        self._pool = pool
        self._transaction: beerstream1.Transaction | None = None

    @property
    def _active(self) -> beerstream1.Transaction:
        assert self._transaction is not None, "Transaction used outside its block."

        return self._transaction

    async def __aenter__(self) -> MySQLTransaction:
        self._transaction = await self._pool.begin()

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        transaction = self._active
        self._transaction = None

        if exc_type is None:
            await transaction.commit()
        else:
            await transaction.rollback()

    @override
    async def fetch_one(
        self, query: str, values: MySQLValues | None = None
    ) -> MySQLRow | None:
        return await self._active.fetch_one(query, values)

    @override
    async def fetch_all(
        self, query: str, values: MySQLValues | None = None
    ) -> list[MySQLRow]:
        return list(await self._active.fetch_all(query, values))

    @override
    async def fetch_val(
        self, query: str, values: MySQLValues | None = None
    ) -> MySQLValue:
        return await self._active.fetch_val(query, values)

    @override
    async def execute(
        self, query: str, values: MySQLValues | None = None
    ) -> ExecuteResult:
        return await self._active.execute(query, values)


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

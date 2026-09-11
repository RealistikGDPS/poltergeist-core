import asyncio
import os
from abc import ABC
from abc import abstractmethod
from pathlib import Path
from typing import override

from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)


class ImplementsStorage(ABC):
    """Object storage for large blobs (level strings, replays, account saves)."""

    @abstractmethod
    async def load(self, key: str) -> bytes | None: ...

    @abstractmethod
    async def save(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...


def _read(path: Path) -> bytes | None:
    if not path.is_file():
        return None

    return path.read_bytes()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _remove(path: Path) -> None:
    path.unlink(missing_ok=True)


class LocalStorage(ImplementsStorage):
    """Stores objects on the local filesystem under a root directory. File I/O
    runs in a worker thread so the event loop never blocks."""

    def __init__(self, root: str) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        return self._root.joinpath(*key.split("/"))

    @override
    async def load(self, key: str) -> bytes | None:
        return await asyncio.to_thread(_read, self._path(key))

    @override
    async def save(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(_write, self._path(key), data)

    @override
    async def delete(self, key: str) -> None:
        await asyncio.to_thread(_remove, self._path(key))


def default() -> ImplementsStorage:
    # Local import keeps this module importable without configuration.
    from poltergeist_core import settings

    return LocalStorage(settings.APP_STORAGE_PATH)

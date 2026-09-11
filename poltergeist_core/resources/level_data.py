from datetime import datetime

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import JsonIntList
from poltergeist_core.resources._common import Model


class LevelData(Model):
    level_id: int
    size_bytes: int
    sha1: bytes
    extra_string: str
    song_ids: JsonIntList
    sfx_ids: JsonIntList
    has_replay: bool
    updated_at: datetime


class LevelDataRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_level_id(self, level_id: int) -> LevelData | None:
        row = await self._mysql.fetch_one(
            "SELECT level_id, size_bytes, sha1, extra_string, song_ids, sfx_ids, "
            "has_replay, updated_at FROM level_data WHERE level_id = %(id)s",
            {"id": level_id},
        )

        return None if row is None else LevelData.model_validate(row)

    async def upsert(
        self,
        level_id: int,
        *,
        size_bytes: int,
        sha1: bytes,
        extra_string: str,
        song_ids: str,
        sfx_ids: str,
        has_replay: bool,
    ) -> None:
        """`song_ids` and `sfx_ids` are JSON array text."""

        await self._mysql.execute(
            "INSERT INTO level_data (level_id, size_bytes, sha1, extra_string, "
            "song_ids, sfx_ids, has_replay) VALUES (%(id)s, %(size)s, %(sha1)s, "
            "%(extra)s, %(songs)s, %(sfx)s, %(replay)s) ON DUPLICATE KEY UPDATE "
            "size_bytes = VALUES(size_bytes), sha1 = VALUES(sha1), "
            "extra_string = VALUES(extra_string), song_ids = VALUES(song_ids), "
            "sfx_ids = VALUES(sfx_ids), has_replay = VALUES(has_replay)",
            {
                "id": level_id,
                "size": size_bytes,
                "sha1": sha1,
                "extra": extra_string,
                "songs": song_ids,
                "sfx": sfx_ids,
                "replay": has_replay,
            },
        )

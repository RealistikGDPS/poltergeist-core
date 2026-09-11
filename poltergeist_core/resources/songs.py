from datetime import datetime
from enum import StrEnum

from gdformat.enums import NewBadge
from gdformat.enums import Nong

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset
from poltergeist_core.resources._common import placeholders
from poltergeist_core.utilities import clock

_ARTIST_COLUMNS = "id, name, youtube_channel, website, scouted"
_SONG_COLUMNS = (
    "s.id, s.name, s.artist_id, a.name AS artist_name, "
    "a.youtube_channel AS artist_youtube_channel, a.scouted AS artist_scouted, "
    "s.size_bytes, s.url, s.source, s.video_id, s.priority, s.nong, s.is_new, "
    "s.new_badge, s.soundtrack_url, s.uploaded_by_user_id, s.disabled_at"
)
_SONG_FROM = "FROM songs s JOIN artists a ON a.id = s.artist_id"


class SongSource(StrEnum):
    NEWGROUNDS = "newgrounds"
    LIBRARY = "library"
    CUSTOM = "custom"


class Artist(Model):
    id: int
    name: str
    youtube_channel: str
    website: str
    scouted: bool


class Song(Model):
    id: int
    name: str
    artist_id: int
    artist_name: str
    artist_youtube_channel: str
    artist_scouted: bool
    size_bytes: int
    url: str
    source: SongSource
    video_id: str
    priority: int
    nong: Nong
    is_new: bool
    new_badge: NewBadge
    soundtrack_url: str
    uploaded_by_user_id: int | None
    disabled_at: datetime | None


class ArtistRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_id(self, artist_id: int) -> Artist | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_ARTIST_COLUMNS} FROM artists WHERE id = %(id)s "
            "AND deleted_at IS NULL",
            {"id": artist_id},
        )

        return None if row is None else Artist.model_validate(row)

    async def find_by_name(self, name: str) -> Artist | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_ARTIST_COLUMNS} FROM artists WHERE name = %(name)s "
            "AND deleted_at IS NULL LIMIT 1",
            {"name": name},
        )

        return None if row is None else Artist.model_validate(row)

    async def upsert_upstream(
        self, artist_id: int, name: str, youtube_channel: str, *, scouted: bool
    ) -> None:
        await self._mysql.execute(
            "INSERT INTO artists (id, name, youtube_channel, scouted) "
            "VALUES (%(id)s, %(name)s, %(youtube)s, %(scouted)s) "
            "ON DUPLICATE KEY UPDATE name = VALUES(name), "
            "youtube_channel = VALUES(youtube_channel), scouted = VALUES(scouted)",
            {
                "id": artist_id,
                "name": name,
                "youtube": youtube_channel,
                "scouted": scouted,
            },
        )

    async def create(self, name: str, youtube_channel: str, website: str) -> int:
        result = await self._mysql.execute(
            "INSERT INTO artists (name, youtube_channel, website) "
            "VALUES (%(name)s, %(youtube)s, %(website)s)",
            {"name": name, "youtube": youtube_channel, "website": website},
        )

        return result.last_row_id

    async def list_top(self, page: int, size: int) -> list[Artist]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_ARTIST_COLUMNS} FROM artists a WHERE a.deleted_at IS NULL "
            "ORDER BY (SELECT COUNT(*) FROM songs s JOIN levels l "
            "ON l.custom_song_id = s.id WHERE s.artist_id = a.id AND l.stars > 0 "
            "AND l.deleted_at IS NULL) DESC, a.name "
            "LIMIT %(limit)s OFFSET %(offset)s",
            {"limit": size, "offset": offset(page, size)},
        )

        return [Artist.model_validate(row) for row in rows]

    async def count(self) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM artists WHERE deleted_at IS NULL"
        )

        return count


class SongRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_id(self, song_id: int) -> Song | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_SONG_COLUMNS} {_SONG_FROM} WHERE s.id = %(id)s "
            "AND s.deleted_at IS NULL",
            {"id": song_id},
        )

        return None if row is None else Song.model_validate(row)

    async def find_many_by_ids(self, song_ids: list[int]) -> list[Song]:
        if not song_ids:
            return []

        sql, values = placeholders(song_ids, "id")

        rows = await self._mysql.fetch_all(
            f"SELECT {_SONG_COLUMNS} {_SONG_FROM} WHERE s.id IN ({sql}) "
            "AND s.deleted_at IS NULL",
            values,
        )

        return [Song.model_validate(row) for row in rows]

    async def list_page(self, *, query: str, page: int, size: int) -> list[Song]:
        rows = await self._mysql.fetch_all(
            f"SELECT {_SONG_COLUMNS} {_SONG_FROM} WHERE s.deleted_at IS NULL "
            "AND (s.name LIKE %(pattern)s OR a.name LIKE %(pattern)s OR s.id = "
            "%(exact)s) "
            "ORDER BY s.id DESC LIMIT %(limit)s OFFSET %(offset)s",
            {
                "pattern": f"%{query}%",
                "exact": int(query) if query.isdecimal() else 0,
                "limit": size,
                "offset": offset(page, size),
            },
        )

        return [Song.model_validate(row) for row in rows]

    async def count_page(self, *, query: str) -> int:
        count: int = await self._mysql.fetch_val(
            f"SELECT COUNT(*) {_SONG_FROM} WHERE s.deleted_at IS NULL "
            "AND (s.name LIKE %(pattern)s OR a.name LIKE %(pattern)s OR s.id = "
            "%(exact)s)",
            {"pattern": f"%{query}%", "exact": int(query) if query.isdecimal() else 0},
        )

        return count

    async def upsert_upstream(
        self,
        song_id: int,
        *,
        name: str,
        artist_id: int,
        size_bytes: int,
        url: str,
        source: SongSource,
        video_id: str,
        priority: int,
        nong: Nong,
        is_new: bool,
        new_badge: NewBadge,
        soundtrack_url: str,
    ) -> None:
        await self._mysql.execute(
            "INSERT INTO songs (id, name, artist_id, size_bytes, url, source, "
            "video_id, "
            "priority, nong, is_new, new_badge, soundtrack_url) VALUES (%(id)s, "
            "%(name)s, %(artist)s, %(size)s, %(url)s, %(source)s, %(video)s, "
            "%(priority)s, %(nong)s, %(is_new)s, %(badge)s, %(soundtrack)s) "
            "ON DUPLICATE KEY UPDATE name = VALUES(name), artist_id = "
            "VALUES(artist_id), "
            "size_bytes = VALUES(size_bytes), url = VALUES(url), "
            "video_id = VALUES(video_id), priority = VALUES(priority), "
            "nong = VALUES(nong), is_new = VALUES(is_new), "
            "new_badge = VALUES(new_badge), soundtrack_url = VALUES(soundtrack_url)",
            {
                "id": song_id,
                "name": name,
                "artist": artist_id,
                "size": size_bytes,
                "url": url,
                "source": source.value,
                "video": video_id,
                "priority": priority,
                "nong": int(nong),
                "is_new": is_new,
                "badge": int(new_badge),
                "soundtrack": soundtrack_url,
            },
        )

    async def create_custom(
        self,
        *,
        name: str,
        artist_id: int,
        size_bytes: int,
        url: str,
        uploaded_by_user_id: int | None,
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO songs (name, artist_id, size_bytes, url, source, "
            "uploaded_by_user_id) VALUES (%(name)s, %(artist)s, %(size)s, %(url)s, "
            "%(source)s, %(by)s)",
            {
                "name": name,
                "artist": artist_id,
                "size": size_bytes,
                "url": url,
                "source": SongSource.CUSTOM.value,
                "by": uploaded_by_user_id,
            },
        )

        return result.last_row_id

    async def set_disabled(self, song_id: int, *, disabled: bool) -> None:
        await self._mysql.execute(
            "UPDATE songs SET disabled_at = %(at)s WHERE id = %(id)s",
            {"id": song_id, "at": clock.now() if disabled else None},
        )

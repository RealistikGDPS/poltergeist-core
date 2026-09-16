from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from gdformat.enums import Difficulty
from gdformat.enums import Length
from gdformat.enums import Rating
from gdformat.enums import TimelyType
from gdformat.enums import Visibility

from poltergeist_core.adapters.mysql import ImplementsMySQL
from poltergeist_core.adapters.mysql import MySQLValue
from poltergeist_core.resources._common import Model
from poltergeist_core.resources._common import offset
from poltergeist_core.resources._common import placeholders
from poltergeist_core.resources.users import UserKind
from poltergeist_core.utilities import clock

_COLUMNS = (
    "l.id, l.user_id, l.name, l.description, l.version, l.length, "
    "l.official_song_id, l.custom_song_id, l.game_version, l.binary_version, "
    "l.visibility, l.two_player, l.low_detail_mode, l.original_id, "
    "l.official_id, l.copyable, l.copy_password, l.object_count, l.coins, "
    "l.coins_verified, "
    "l.requested_stars, l.editor_seconds, l.editor_seconds_copies, "
    "l.verification_frames, l.downloads, l.likes, l.difficulty, l.stars, "
    "l.feature_order, l.rating, l.rated_at, l.rated_by_user_id, l.update_locked, "
    "l.uploaded_at, l.updated_at"
)
_COUNT_CAP = 9999


class LevelOrder(StrEnum):
    DOWNLOADS = "downloads"
    LIKES = "likes"
    UPLOADED = "uploaded"
    FEATURED = "featured"
    RATED = "rated"
    GIVEN = "given"
    TIMELY = "timely"
    SUGGESTED = "suggested"
    REPORTED = "reported"


class Level(Model):
    id: int
    user_id: int
    name: str
    description: str
    version: int
    length: Length
    official_song_id: int
    custom_song_id: int | None
    game_version: int
    binary_version: int
    visibility: Visibility
    two_player: bool
    low_detail_mode: bool
    original_id: int | None
    official_id: int | None
    copyable: bool
    copy_password: int | None
    object_count: int
    coins: int
    coins_verified: bool
    requested_stars: int
    editor_seconds: int
    editor_seconds_copies: int
    verification_frames: int
    downloads: int
    likes: int
    difficulty: Difficulty
    stars: int
    feature_order: int
    rating: Rating
    rated_at: datetime | None
    rated_by_user_id: int | None
    update_locked: bool
    uploaded_at: datetime
    updated_at: datetime

    @property
    def is_rated(self) -> bool:
        return self.stars > 0

    @property
    def is_featured(self) -> bool:
        return self.feature_order > 0


class RatedLevel(Model):
    id: int
    stars: int


@dataclass(frozen=True, slots=True, kw_only=True)
class LevelSearch:
    """Every constraint a level listing can carry. `friend_ids` and
    `viewer_user_id` decide which friends-only and unlisted levels are visible."""

    order: LevelOrder
    page: int
    size: int
    viewer_user_id: int | None = None
    friend_ids: tuple[int, ...] = ()
    include_unlisted: bool = False
    include_all_visibilities: bool = False
    level_ids: tuple[int, ...] | None = None
    creator_ids: tuple[int, ...] | None = None
    name_prefix: str | None = None
    difficulties: tuple[Difficulty, ...] | None = None
    lengths: tuple[Length, ...] | None = None
    exclude_ids: tuple[int, ...] | None = None
    only_ids: tuple[int, ...] | None = None
    featured: bool = False
    player_creators_only: bool = False
    original: bool = False
    two_player: bool = False
    coins: bool = False
    ratings: tuple[Rating, ...] | None = None
    rated: bool = False
    unrated: bool = False
    official_song_id: int | None = None
    custom_song_id: int | None = None
    uploaded_after: datetime | None = None
    min_objects: int | None = None
    timely_type: TimelyType | None = None
    pending_suggestions: bool = False
    open_reports: bool = False


def _order_sql(search: LevelSearch, values: dict[str, MySQLValue]) -> str:
    match search.order:
        case LevelOrder.DOWNLOADS:
            return "l.downloads DESC, l.id DESC"
        case LevelOrder.LIKES:
            return "l.likes DESC, l.id DESC"
        case LevelOrder.UPLOADED:
            return "l.uploaded_at DESC, l.id DESC"
        case LevelOrder.FEATURED:
            return "l.feature_order DESC, l.id DESC"
        case LevelOrder.RATED:
            return "l.rated_at DESC, l.id DESC"
        case LevelOrder.GIVEN:
            if not search.level_ids:
                return "l.id DESC"

            sql, given = placeholders(list(search.level_ids), "order")
            values.update(given)

            return f"FIELD(l.id, {sql})"
        case LevelOrder.TIMELY:
            return (
                "(SELECT MAX(t.sequence) FROM timely_levels t WHERE t.level_id = l.id "
                "AND t.type = %(timely_type)s AND t.deleted_at IS NULL) DESC"
            )
        case LevelOrder.SUGGESTED:
            return (
                "(SELECT MAX(s.created_at) FROM level_suggestions s "
                "WHERE s.level_id = l.id AND s.deleted_at IS NULL) DESC"
            )
        case LevelOrder.REPORTED:
            return (
                "(SELECT COUNT(*) FROM level_reports r WHERE r.level_id = l.id "
                "AND r.resolved_at IS NULL) DESC, l.id DESC"
            )


def _visibility_sql(search: LevelSearch, values: dict[str, MySQLValue]) -> str | None:
    if search.include_all_visibilities:
        return None

    parts = [f"l.visibility = {int(Visibility.PUBLIC)}"]

    if search.include_unlisted:
        parts.append(f"l.visibility = {int(Visibility.UNLISTED)}")

    if search.friend_ids:
        sql, friends = placeholders(list(search.friend_ids), "friend")
        values.update(friends)
        parts.append(
            f"(l.visibility = {int(Visibility.FRIENDS)} AND l.user_id IN ({sql}))"
        )

    if search.viewer_user_id is not None:
        values["viewer"] = search.viewer_user_id
        parts.append("l.user_id = %(viewer)s")

    return "(" + " OR ".join(parts) + ")"


def _where_sql(search: LevelSearch) -> tuple[str, dict[str, MySQLValue]]:
    values: dict[str, MySQLValue] = {}
    clauses = ["l.deleted_at IS NULL"]
    visibility = _visibility_sql(search, values)

    if visibility is not None:
        clauses.append(visibility)

    if search.level_ids is not None:
        if not search.level_ids:
            clauses.append("FALSE")
        else:
            sql, given = placeholders(list(search.level_ids), "level")
            values.update(given)
            clauses.append(f"l.id IN ({sql})")

    if search.creator_ids is not None:
        if not search.creator_ids:
            clauses.append("FALSE")
        else:
            sql, given = placeholders(list(search.creator_ids), "creator")
            values.update(given)
            clauses.append(f"l.user_id IN ({sql})")

    if search.name_prefix is not None:
        values["name_pattern"] = f"{search.name_prefix}%"
        clauses.append("l.name LIKE %(name_pattern)s")

    if search.difficulties is not None:
        sql, given = placeholders([int(d) for d in search.difficulties], "difficulty")
        values.update(given)
        clauses.append(f"l.difficulty IN ({sql})")

    if search.lengths is not None:
        sql, given = placeholders([int(length) for length in search.lengths], "length")
        values.update(given)
        clauses.append(f"l.length IN ({sql})")

    if search.exclude_ids:
        sql, given = placeholders(list(search.exclude_ids), "exclude")
        values.update(given)
        clauses.append(f"l.id NOT IN ({sql})")

    if search.only_ids is not None:
        if not search.only_ids:
            clauses.append("FALSE")
        else:
            sql, given = placeholders(list(search.only_ids), "only")
            values.update(given)
            clauses.append(f"l.id IN ({sql})")

    if search.featured:
        clauses.append("l.feature_order > 0")

    if search.player_creators_only:
        values["player_kind"] = UserKind.PLAYER.value
        clauses.append(
            "l.user_id NOT IN (SELECT u.id FROM users u WHERE u.kind <> "
            "%(player_kind)s)"
        )

    if search.original:
        clauses.append("l.original_id IS NULL")

    if search.two_player:
        clauses.append("l.two_player")

    if search.coins:
        clauses.append("l.coins_verified")

    if search.ratings is not None:
        sql, given = placeholders([int(rating) for rating in search.ratings], "rating")
        values.update(given)
        clauses.append(f"l.rating IN ({sql})")

    if search.rated:
        clauses.append("l.stars > 0")

    if search.unrated:
        clauses.append("l.stars = 0")

    if search.official_song_id is not None:
        values["official_song"] = search.official_song_id
        clauses.append(
            "l.custom_song_id IS NULL AND l.official_song_id = %(official_song)s"
        )

    if search.custom_song_id is not None:
        values["custom_song"] = search.custom_song_id
        clauses.append("l.custom_song_id = %(custom_song)s")

    if search.uploaded_after is not None:
        values["uploaded_after"] = search.uploaded_after
        clauses.append("l.uploaded_at >= %(uploaded_after)s")

    if search.min_objects is not None:
        values["min_objects"] = search.min_objects
        clauses.append("l.object_count >= %(min_objects)s")

    if search.timely_type is not None:
        values["timely_type"] = int(search.timely_type)
        values["timely_now"] = clock.now()
        clauses.append(
            "l.id IN (SELECT t.level_id FROM timely_levels t WHERE t.type = "
            "%(timely_type)s "
            "AND t.deleted_at IS NULL AND t.starts_at <= %(timely_now)s)"
        )

    if search.pending_suggestions:
        clauses.append(
            "l.id IN (SELECT s.level_id FROM level_suggestions s WHERE s.deleted_at "
            "IS NULL)"
        )

    if search.open_reports:
        clauses.append(
            "l.id IN (SELECT r.level_id FROM level_reports r WHERE r.resolved_at IS "
            "NULL)"
        )

    return " AND ".join(clauses), values


class LevelRepository:
    __slots__ = ("_mysql",)

    def __init__(self, mysql: ImplementsMySQL) -> None:
        self._mysql = mysql

    async def find_by_id(self, level_id: int) -> Level | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM levels l WHERE l.id = %(id)s AND l.deleted_at IS "
            f"NULL",
            {"id": level_id},
        )

        return None if row is None else Level.model_validate(row)

    async def find_many_by_ids(self, level_ids: list[int]) -> list[Level]:
        if not level_ids:
            return []

        sql, values = placeholders(level_ids, "id")

        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM levels l WHERE l.id IN ({sql}) "
            "AND l.deleted_at IS NULL",
            values,
        )

        return [Level.model_validate(row) for row in rows]

    async def find_by_user_and_name(self, user_id: int, name: str) -> Level | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM levels l WHERE l.user_id = %(user)s "
            "AND l.name = %(name)s AND l.deleted_at IS NULL ORDER BY l.id DESC LIMIT 1",
            {"user": user_id, "name": name},
        )

        return None if row is None else Level.model_validate(row)

    async def find_by_official_id(self, official_id: int) -> Level | None:
        row = await self._mysql.fetch_one(
            f"SELECT {_COLUMNS} FROM levels l WHERE l.official_id = %(id)s "
            "AND l.deleted_at IS NULL",
            {"id": official_id},
        )

        return None if row is None else Level.model_validate(row)

    async def official_id_claimed(self, official_id: int) -> bool:
        """Deleted reuploads keep their claim; the unique key holds either way."""

        claimed: int = await self._mysql.fetch_val(
            "SELECT EXISTS(SELECT 1 FROM levels WHERE official_id = %(id)s)",
            {"id": official_id},
        )

        return bool(claimed)

    async def search(self, search: LevelSearch) -> list[Level]:
        where, values = _where_sql(search)
        order = _order_sql(search, values)

        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM levels l WHERE {where} ORDER BY {order} "
            "LIMIT %(limit)s OFFSET %(offset)s",
            {
                **values,
                "limit": search.size,
                "offset": offset(search.page, search.size),
            },
        )

        return [Level.model_validate(row) for row in rows]

    async def count(self, search: LevelSearch) -> int:
        """Capped, so a broad listing never scans the whole table."""

        where, values = _where_sql(search)

        count: int = await self._mysql.fetch_val(
            f"SELECT COUNT(*) FROM (SELECT 1 FROM levels l WHERE {where} "
            f"LIMIT {_COUNT_CAP}) c",
            values,
        )

        return count

    async def create(
        self,
        *,
        user_id: int,
        name: str,
        description: str,
        version: int,
        length: Length,
        official_song_id: int,
        custom_song_id: int | None,
        game_version: int,
        binary_version: int,
        visibility: Visibility,
        two_player: bool,
        low_detail_mode: bool,
        original_id: int | None,
        official_id: int | None,
        copyable: bool,
        copy_password: int | None,
        object_count: int,
        coins: int,
        requested_stars: int,
        editor_seconds: int,
        editor_seconds_copies: int,
        verification_frames: int,
    ) -> int:
        result = await self._mysql.execute(
            "INSERT INTO levels (user_id, name, description, version, length, "
            "official_song_id, custom_song_id, game_version, binary_version, "
            "visibility, two_player, low_detail_mode, original_id, official_id, "
            "copyable, copy_password, object_count, coins, requested_stars, "
            "editor_seconds, editor_seconds_copies, verification_frames) VALUES "
            "(%(user_id)s, %(name)s, %(description)s, %(version)s, %(length)s, "
            "%(official_song)s, %(custom_song)s, %(game_version)s, "
            "%(binary_version)s, %(visibility)s, %(two_player)s, %(ldm)s, "
            "%(original)s, %(official)s, %(copyable)s, %(password)s, "
            "%(objects)s, %(coins)s, %(requested_stars)s, %(editor)s, "
            "%(editor_copies)s, %(frames)s)",
            {
                "user_id": user_id,
                "name": name,
                "description": description,
                "version": version,
                "length": int(length),
                "official_song": official_song_id,
                "custom_song": custom_song_id,
                "game_version": game_version,
                "binary_version": binary_version,
                "visibility": int(visibility),
                "two_player": two_player,
                "ldm": low_detail_mode,
                "original": original_id,
                "official": official_id,
                "copyable": copyable,
                "password": copy_password,
                "objects": object_count,
                "coins": coins,
                "requested_stars": requested_stars,
                "editor": editor_seconds,
                "editor_copies": editor_seconds_copies,
                "frames": verification_frames,
            },
        )

        return result.last_row_id

    async def update(
        self,
        level_id: int,
        *,
        description: str,
        version: int,
        length: Length,
        official_song_id: int,
        custom_song_id: int | None,
        game_version: int,
        binary_version: int,
        visibility: Visibility,
        two_player: bool,
        low_detail_mode: bool,
        copyable: bool,
        copy_password: int | None,
        object_count: int,
        coins: int,
        requested_stars: int,
        editor_seconds: int,
        editor_seconds_copies: int,
        verification_frames: int,
    ) -> None:
        await self._mysql.execute(
            "UPDATE levels SET description = %(description)s, version = %(version)s, "
            "length = %(length)s, official_song_id = %(official_song)s, "
            "custom_song_id = %(custom_song)s, game_version = %(game_version)s, "
            "binary_version = %(binary_version)s, visibility = %(visibility)s, "
            "two_player = %(two_player)s, low_detail_mode = %(ldm)s, "
            "copyable = %(copyable)s, copy_password = %(password)s, "
            "object_count = %(objects)s, coins = %(coins)s, "
            "requested_stars = %(requested_stars)s, editor_seconds = %(editor)s, "
            "editor_seconds_copies = %(editor_copies)s, "
            "verification_frames = %(frames)s, updated_at = %(now)s WHERE id = %(id)s",
            {
                "id": level_id,
                "description": description,
                "version": version,
                "length": int(length),
                "official_song": official_song_id,
                "custom_song": custom_song_id,
                "game_version": game_version,
                "binary_version": binary_version,
                "visibility": int(visibility),
                "two_player": two_player,
                "ldm": low_detail_mode,
                "copyable": copyable,
                "password": copy_password,
                "objects": object_count,
                "coins": coins,
                "requested_stars": requested_stars,
                "editor": editor_seconds,
                "editor_copies": editor_seconds_copies,
                "frames": verification_frames,
                "now": clock.now(),
            },
        )

    async def update_description(self, level_id: int, description: str) -> None:
        await self._mysql.execute(
            "UPDATE levels SET description = %(description)s WHERE id = %(id)s",
            {"id": level_id, "description": description},
        )

    async def soft_delete(self, level_id: int) -> None:
        await self._mysql.execute(
            "UPDATE levels SET deleted_at = %(now)s WHERE id = %(id)s",
            {"id": level_id, "now": clock.now()},
        )

    async def increment_downloads(self, level_id: int) -> None:
        await self._mysql.execute(
            "UPDATE levels SET downloads = downloads + 1 WHERE id = %(id)s",
            {"id": level_id},
        )

    async def add_likes(self, level_id: int, delta: int) -> None:
        await self._mysql.execute(
            "UPDATE levels SET likes = likes + %(delta)s WHERE id = %(id)s",
            {"id": level_id, "delta": delta},
        )

    async def rate(
        self,
        level_id: int,
        *,
        stars: int,
        difficulty: Difficulty,
        coins_verified: bool,
        feature_order: int,
        rating: Rating,
        rated_by_user_id: int | None,
    ) -> None:
        await self._mysql.execute(
            "UPDATE levels SET stars = %(stars)s, difficulty = %(difficulty)s, "
            "coins_verified = %(coins_verified)s, feature_order = %(feature_order)s, "
            "rating = %(rating)s, rated_at = %(rated_at)s, "
            "rated_by_user_id = %(rated_by)s WHERE id = %(id)s",
            {
                "id": level_id,
                "stars": stars,
                "difficulty": int(difficulty),
                "coins_verified": coins_verified,
                "feature_order": feature_order,
                "rating": int(rating),
                "rated_at": clock.now() if stars > 0 else None,
                "rated_by": rated_by_user_id,
            },
        )

    async def set_difficulty(self, level_id: int, difficulty: Difficulty) -> None:
        await self._mysql.execute(
            "UPDATE levels SET difficulty = %(difficulty)s WHERE id = %(id)s",
            {"id": level_id, "difficulty": int(difficulty)},
        )

    async def set_visibility(self, level_id: int, visibility: Visibility) -> None:
        await self._mysql.execute(
            "UPDATE levels SET visibility = %(visibility)s WHERE id = %(id)s",
            {"id": level_id, "visibility": int(visibility)},
        )

    async def set_update_locked(self, level_id: int, *, locked: bool) -> None:
        await self._mysql.execute(
            "UPDATE levels SET update_locked = %(locked)s WHERE id = %(id)s",
            {"id": level_id, "locked": locked},
        )

    async def transfer(self, level_id: int, user_id: int) -> None:
        await self._mysql.execute(
            "UPDATE levels SET user_id = %(user)s WHERE id = %(id)s "
            "AND deleted_at IS NULL",
            {"id": level_id, "user": user_id},
        )

    async def next_feature_order(self) -> int:
        highest: int | None = await self._mysql.fetch_val(
            "SELECT MAX(feature_order) FROM levels"
        )

        return (highest or 0) + 1

    async def creator_points_of(self, user_id: int) -> int:
        """Rated 1, featured 2, epic 3, legendary 4, mythic 5: the rating
        column counts 0 to 3 in that order, so it is the bonus itself."""

        points: int = await self._mysql.fetch_val(
            "SELECT COALESCE(SUM((stars > 0) + (feature_order > 0) + rating), 0) "
            "FROM levels WHERE user_id = %(id)s AND deleted_at IS NULL",
            {"id": user_id},
        )

        return int(points)

    async def count_by_user(self, user_id: int) -> int:
        count: int = await self._mysql.fetch_val(
            "SELECT COUNT(*) FROM levels WHERE user_id = %(id)s AND deleted_at IS NULL",
            {"id": user_id},
        )

        return count

    async def list_rated(self) -> list[RatedLevel]:
        rows = await self._mysql.fetch_all(
            "SELECT id, stars FROM levels WHERE stars > 0 AND deleted_at IS NULL "
            "ORDER BY id"
        )

        return [RatedLevel.model_validate(row) for row in rows]

    async def find_demon_ids(self, level_ids: list[int]) -> list[Level]:
        if not level_ids:
            return []

        sql, values = placeholders(level_ids, "id")

        rows = await self._mysql.fetch_all(
            f"SELECT {_COLUMNS} FROM levels l WHERE l.id IN ({sql}) "
            f"AND l.difficulty >= {int(Difficulty.EASY_DEMON)} AND l.deleted_at IS "
            f"NULL",
            values,
        )

        return [Level.model_validate(row) for row in rows]

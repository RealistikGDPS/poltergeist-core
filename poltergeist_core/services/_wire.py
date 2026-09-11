from datetime import datetime

from gdformat import encoding
from gdformat import objects
from gdformat.enums import Difficulty
from gdformat.enums import ModLevel
from gdformat.enums import Rating

from poltergeist_core.resources import AccountComment
from poltergeist_core.resources import ChestClaim
from poltergeist_core.resources import Comment
from poltergeist_core.resources import FriendRequest
from poltergeist_core.resources import Gauntlet
from poltergeist_core.resources import Level
from poltergeist_core.resources import LevelData
from poltergeist_core.resources import LevelList
from poltergeist_core.resources import MapPack
from poltergeist_core.resources import Message
from poltergeist_core.resources import Quest
from poltergeist_core.resources import Song
from poltergeist_core.resources import User
from poltergeist_core.resources import UserStats
from poltergeist_core.utilities import clock

_BYTES_PER_MB = 1_048_576


def age(moment: datetime) -> str:
    return encoding.describe_age(clock.seconds_since(moment))


def colour(packed: int) -> objects.Colour:
    return objects.Colour((packed >> 16) & 0xFF, (packed >> 8) & 0xFF, packed & 0xFF)


def display_icon(stats: UserStats) -> objects.DisplayIcon:
    return objects.DisplayIcon(
        icon_id=stats.selected_icon,
        icon_type=stats.icon_type,
        colour1=stats.colour1,
        colour2=stats.colour2,
        colour3=stats.colour3,
        glow=stats.glow,
    )


def player(user: User, stats: UserStats) -> objects.Player:
    return objects.Player(
        name=user.username,
        user_id=user.id,
        account_id=user.id,
        icon=display_icon(stats),
    )


def user_ref(user: User) -> objects.UserRef:
    return objects.UserRef(user_id=user.id, account_id=user.id, name=user.username)


def user_preview(user: User, stats: UserStats, rank: int) -> objects.UserPreview:
    return objects.UserPreview(
        name=user.username,
        user_id=user.id,
        account_id=user.id,
        icon=display_icon(stats),
        stars=stats.stars,
        moons=stats.moons,
        demons=stats.demons,
        diamonds=stats.diamonds,
        secret_coins=stats.secret_coins,
        user_coins=stats.user_coins,
        creator_points=stats.creator_points,
        rank=rank,
    )


def player_stats(stats: UserStats) -> objects.PlayerStats:
    return objects.PlayerStats(
        stars=stats.stars,
        moons=stats.moons,
        demons=stats.demons,
        creator_points=stats.creator_points,
    )


def icon_set(stats: UserStats) -> objects.IconSet:
    return objects.IconSet(
        cube=stats.icon_cube,
        ship=stats.icon_ship,
        ball=stats.icon_ball,
        ufo=stats.icon_ufo,
        wave=stats.icon_wave,
        robot=stats.icon_robot,
        spider=stats.icon_spider,
        swing=stats.icon_swing,
        jetpack=stats.icon_jetpack,
        explosion=stats.icon_explosion,
    )


def demon_stats(stats: UserStats) -> objects.DemonStats:
    return objects.DemonStats(
        easy=stats.demons_easy,
        medium=stats.demons_medium,
        hard=stats.demons_hard,
        insane=stats.demons_insane,
        extreme=stats.demons_extreme,
        easy_platformer=stats.demons_easy_platformer,
        medium_platformer=stats.demons_medium_platformer,
        hard_platformer=stats.demons_hard_platformer,
        insane_platformer=stats.demons_insane_platformer,
        extreme_platformer=stats.demons_extreme_platformer,
        weekly=stats.demons_weekly,
        gauntlet=stats.demons_gauntlet,
    )


def classic_stats(stats: UserStats) -> objects.ClassicStats:
    return objects.ClassicStats(
        auto=stats.classic_auto,
        easy=stats.classic_easy,
        normal=stats.classic_normal,
        hard=stats.classic_hard,
        harder=stats.classic_harder,
        insane=stats.classic_insane,
        daily=stats.classic_daily,
        gauntlet=stats.classic_gauntlet,
    )


def platformer_stats(stats: UserStats) -> objects.PlatformerStats:
    return objects.PlatformerStats(
        auto=stats.platformer_auto,
        easy=stats.platformer_easy,
        normal=stats.platformer_normal,
        hard=stats.platformer_hard,
        harder=stats.platformer_harder,
        insane=stats.platformer_insane,
        event=stats.platformer_event,
    )


def privacy(user: User) -> objects.Privacy:
    return objects.Privacy(
        messages=user.message_privacy,
        friend_requests=user.friend_request_privacy,
        comment_history=user.comment_history_privacy,
    )


def socials(user: User) -> objects.Socials:
    return objects.Socials(
        youtube=user.youtube,
        twitter=user.twitter,
        twitch=user.twitch,
        discord=user.discord,
        instagram=user.instagram,
        tiktok=user.tiktok,
    )


def level_password(level: Level) -> str | None:
    if not level.copyable:
        return None

    if level.copy_password is None:
        return ""

    return str(level.copy_password)


def level_preview(level: Level, *, gauntlet: bool = False) -> objects.LevelPreview:
    return objects.LevelPreview(
        id=level.id,
        name=level.name,
        description=level.description,
        version=level.version,
        creator_id=level.user_id,
        difficulty=level.difficulty,
        downloads=level.downloads,
        likes=level.likes,
        length=level.length,
        stars=level.stars,
        feature_score=level.feature_order,
        rating=level.rating,
        official_song=level.official_song_id,
        custom_song_id=level.custom_song_id or 0,
        game_version=level.game_version,
        coins=level.coins,
        verified_coins=level.coins_verified,
        requested_stars=level.requested_stars,
        objects=level.object_count,
        original_id=level.original_id or 0,
        two_player=level.two_player,
        editor_time=level.editor_seconds,
        editor_time_copies=level.editor_seconds_copies,
        gauntlet=gauntlet,
    )


def level_full(
    level: Level,
    data: LevelData,
    level_string: str,
    *,
    timely_id: int | None,
    song_size: int,
) -> objects.Level:
    return objects.Level(
        id=level.id,
        name=level.name,
        description=level.description,
        version=level.version,
        creator_id=level.user_id,
        difficulty=level.difficulty,
        downloads=level.downloads,
        likes=level.likes,
        length=level.length,
        stars=level.stars,
        feature_score=level.feature_order,
        rating=level.rating,
        official_song=level.official_song_id,
        custom_song_id=level.custom_song_id or 0,
        game_version=level.game_version,
        coins=level.coins,
        verified_coins=level.coins_verified,
        requested_stars=level.requested_stars,
        objects=level.object_count,
        original_id=level.original_id or 0,
        two_player=level.two_player,
        editor_time=level.editor_seconds,
        editor_time_copies=level.editor_seconds_copies,
        level_string=level_string,
        uploaded_ago=age(level.uploaded_at),
        updated_ago=age(level.updated_at),
        password=level_password(level),
        extra_string=data.extra_string,
        low_detail_mode=level.low_detail_mode,
        timely_id=timely_id,
        song_ids=tuple(data.song_ids),
        sfx_ids=tuple(data.sfx_ids),
        song_size=song_size,
        verification_time=level.verification_frames,
        uploaded_at=clock.timestamp(level.uploaded_at),
        updated_at=clock.timestamp(level.updated_at),
    )


def song(entry: Song) -> objects.Song:
    return objects.Song(
        id=entry.id,
        name=entry.name,
        artist_id=entry.artist_id,
        artist_name=entry.artist_name,
        size_mb=entry.size_bytes / _BYTES_PER_MB,
        url=entry.url,
        video_id=entry.video_id,
        youtube_channel=entry.artist_youtube_channel,
        scouted=entry.artist_scouted,
        priority=entry.priority,
        nong=entry.nong,
        is_new=entry.is_new,
        new_badge=entry.new_badge,
        soundtrack_url=entry.soundtrack_url,
    )


def level_list(
    entry: LevelList, level_ids: list[int], creator: User
) -> objects.LevelList:
    return objects.LevelList(
        id=entry.id,
        name=entry.name,
        description=entry.description,
        version=entry.version,
        creator_account_id=creator.id,
        creator_name=creator.username,
        level_ids=tuple(level_ids),
        difficulty=entry.difficulty,
        downloads=entry.downloads,
        likes=entry.likes,
        uploaded_at=clock.timestamp(entry.uploaded_at),
        updated_at=clock.timestamp(entry.updated_at),
        rated=entry.is_rated,
        reward_diamonds=entry.reward_diamonds,
        reward_requirement=entry.reward_requirement,
    )


def map_pack(pack: MapPack, level_ids: list[int]) -> objects.MapPack:
    return objects.MapPack(
        id=pack.id,
        name=pack.name,
        level_ids=tuple(level_ids),
        stars=pack.stars,
        coins=pack.coins,
        difficulty=pack.difficulty,
        text_colour=colour(pack.text_colour),
        bar_colour=colour(pack.bar_colour),
    )


def gauntlet(entry: Gauntlet) -> objects.Gauntlet:
    return objects.Gauntlet(id=entry.id, level_ids=tuple(entry.level_ids))


def comment(
    entry: Comment,
    author: User,
    stats: UserStats,
    badge: ModLevel,
    *,
    include_level_id: bool,
) -> objects.Comment:
    chat_colour = None

    if badge is not ModLevel.NONE and author.comment_colour is not None:
        chat_colour = colour(author.comment_colour)

    return objects.Comment(
        id=entry.id,
        content=entry.content,
        author=player(author, stats),
        likes=entry.likes,
        age=age(entry.created_at),
        percent=entry.percent,
        is_spam=entry.is_spam,
        level_id=entry.level_id if include_level_id else None,
        mod_level=badge,
        chat_colour=chat_colour,
    )


def account_comment(entry: AccountComment) -> objects.AccountComment:
    return objects.AccountComment(
        id=entry.id,
        content=entry.content,
        likes=entry.likes,
        age=age(entry.created_at),
    )


def friend_request(
    entry: FriendRequest, peer: User, stats: UserStats
) -> objects.FriendRequest:
    return objects.FriendRequest(
        id=entry.id,
        player=player(peer, stats),
        message=entry.message,
        age=age(entry.created_at),
        is_new=entry.read_at is None,
        stats=player_stats(stats),
    )


def message(
    entry: Message, peer: User, *, outgoing: bool, include_body: bool
) -> objects.Message:
    return objects.Message(
        id=entry.id,
        peer=user_ref(peer),
        subject=entry.subject,
        age=age(entry.created_at),
        read=entry.read_at is not None,
        outgoing=outgoing,
        body=entry.body if include_body else None,
    )


def user_list_entry(
    peer: User, stats: UserStats, *, is_new: bool
) -> objects.UserListEntry:
    return objects.UserListEntry(
        player=player(peer, stats),
        is_new=is_new,
        stats=player_stats(stats),
        message_state=peer.message_privacy,
    )


def quest(entry: Quest, *, wire_id: int) -> objects.Quest:
    return objects.Quest(
        id=wire_id,
        item=entry.item,
        amount=entry.amount,
        diamonds=entry.diamonds,
        name=entry.name,
    )


def chest(claim: ChestClaim | None) -> objects.Chest | None:
    if claim is None:
        return None

    return objects.Chest(
        orbs=claim.orbs,
        diamonds=claim.diamonds,
        shard=claim.shard,
        keys=claim.demon_keys,
    )


def difficulty_for_stars(stars: int, demon: Difficulty | None) -> Difficulty:
    match stars:
        case 0:
            return Difficulty.NA
        case 1:
            return Difficulty.AUTO
        case 2:
            return Difficulty.EASY
        case 3:
            return Difficulty.NORMAL
        case 4 | 5:
            return Difficulty.HARD
        case 6 | 7:
            return Difficulty.HARDER
        case 8 | 9:
            return Difficulty.INSANE
        case _:
            return Difficulty.HARD_DEMON if demon is None else demon


def is_epic_or_better(rating: Rating) -> bool:
    return rating is not Rating.NONE

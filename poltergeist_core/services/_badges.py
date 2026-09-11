from gdformat.enums import ModAccess
from gdformat.enums import ModLevel

from poltergeist_core.resources import Permission
from poltergeist_core.utilities import permissions


def mod_level(granted: frozenset[str]) -> ModLevel:
    if permissions.is_granted(granted, Permission.MOD_BADGE_ELDER):
        return ModLevel.ELDER

    if permissions.is_granted(granted, Permission.MOD_BADGE_MODERATOR):
        return ModLevel.MOD

    if permissions.is_granted(granted, Permission.MOD_BADGE_LEADERBOARD):
        return ModLevel.LEADERBOARD

    return ModLevel.NONE


def mod_access(granted: frozenset[str]) -> ModAccess:
    match mod_level(granted):
        case ModLevel.ELDER:
            return ModAccess.ELDER
        case ModLevel.MOD:
            return ModAccess.MOD
        case ModLevel.LEADERBOARD:
            return ModAccess.LEADERBOARD
        case ModLevel.NONE:
            return ModAccess.DENIED

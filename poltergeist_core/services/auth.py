import asyncio
import re
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus

import bcrypt
from gdformat import codes
from gdformat import crypto
from gdformat.requests import Auth
from gdformat.requests import Client
from gdformat.requests import LoginRequest
from gdformat.requests import RegisterRequest

from poltergeist_core import settings
from poltergeist_core.resources import BanType
from poltergeist_core.resources import User
from poltergeist_core.services._common import GD_FAILURE
from poltergeist_core.services._common import AbstractContext
from poltergeist_core.services._common import ServiceError
from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)

_GJP2_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9 _-]+$")
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_MIN = 3
_USERNAME_MAX = 20
_PASSWORD_MIN = 6
_PASSWORD_MAX = 64
_EMAIL_MAX = 255
_DEFAULT_ROLE = "default"
_FAILED_LOGIN_LIMIT = 10
_FAILED_LOGIN_WINDOW = 600
_REGISTER_LIMIT = 3
_REGISTER_WINDOW = 3600
_REGISTER_ATTEMPT_LIMIT = 30
_MIGRATE_LIMIT = 10
_MIGRATE_WINDOW = 600


class AuthError(ServiceError, StrEnum):
    """`UNAUTHENTICATED` and `BANNED` are for requests carrying an account id;
    the client only understands the specific login codes on the login endpoint."""

    UNAUTHENTICATED = "unauthenticated"
    BANNED = "banned"
    INVALID_CREDENTIALS = "invalid_credentials"
    ACCOUNT_BANNED = "account_banned"
    TOO_MANY_ATTEMPTS = "too_many_attempts"
    NAME_TOO_SHORT = "name_too_short"
    NAME_INVALID = "name_invalid"
    NAME_TAKEN = "name_taken"
    PASSWORD_TOO_SHORT = "password_too_short"
    PASSWORD_INVALID = "password_invalid"
    EMAIL_INVALID = "email_invalid"
    EMAIL_TAKEN = "email_taken"
    USER_NOT_FOUND = "user_not_found"
    ALREADY_MIGRATED = "already_migrated"

    def service(self) -> str:
        return "auth"

    def status_code(self) -> int:
        match self:
            case (
                AuthError.UNAUTHENTICATED
                | AuthError.BANNED
                | AuthError.INVALID_CREDENTIALS
                | AuthError.ACCOUNT_BANNED
            ):
                return HTTPStatus.UNAUTHORIZED
            case AuthError.TOO_MANY_ATTEMPTS:
                return HTTPStatus.TOO_MANY_REQUESTS
            case (
                AuthError.NAME_TAKEN
                | AuthError.EMAIL_TAKEN
                | AuthError.ALREADY_MIGRATED
            ):
                return HTTPStatus.CONFLICT
            case AuthError.USER_NOT_FOUND:
                return HTTPStatus.NOT_FOUND
            case _:
                return HTTPStatus.BAD_REQUEST

    def code(self) -> int:
        match self:
            case AuthError.UNAUTHENTICATED | AuthError.BANNED:
                return GD_FAILURE
            case AuthError.INVALID_CREDENTIALS | AuthError.TOO_MANY_ATTEMPTS:
                return codes.LoginError.WRONG_CREDENTIALS
            case AuthError.ACCOUNT_BANNED:
                return codes.LoginError.ACCOUNT_DISABLED
            case AuthError.NAME_TOO_SHORT:
                return codes.RegisterError.NAME_TOO_SHORT
            case AuthError.NAME_INVALID:
                return codes.RegisterError.NAME_INVALID
            case AuthError.NAME_TAKEN:
                return codes.RegisterError.NAME_TAKEN
            case AuthError.PASSWORD_TOO_SHORT:
                return codes.RegisterError.PASSWORD_TOO_SHORT
            case AuthError.PASSWORD_INVALID:
                return codes.RegisterError.PASSWORD_INVALID
            case AuthError.EMAIL_INVALID:
                return codes.RegisterError.EMAIL_INVALID
            case AuthError.EMAIL_TAKEN:
                return codes.RegisterError.EMAIL_TAKEN
            case AuthError.USER_NOT_FOUND | AuthError.ALREADY_MIGRATED:
                return GD_FAILURE


@dataclass(frozen=True, slots=True)
class Session:
    """An authenticated request: who is asking and from which client."""

    user: User
    client: Client


@dataclass(frozen=True, slots=True)
class LoginResult:
    account_id: int
    user_id: int


def _hash_gjp2(gjp2: str) -> str:
    return bcrypt.hashpw(gjp2.encode(), bcrypt.gensalt()).decode()


def _check_gjp2(gjp2: str, hashed: str) -> bool:
    return bcrypt.checkpw(gjp2.encode(), hashed.encode())


def _check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


async def _verify(ctx: AbstractContext, user: User, gjp2: str) -> bool:
    if not _GJP2_PATTERN.match(gjp2):
        return False

    if await ctx.sessions.is_verified(user.id, gjp2):
        return True

    within_limit = await ctx.rate_limits.hit(
        "login",
        str(user.id),
        limit=_FAILED_LOGIN_LIMIT,
        window_seconds=_FAILED_LOGIN_WINDOW,
    )

    if not within_limit:
        return False

    credential = await ctx.credentials.find_by_user_id(user.id)

    if credential is None or credential.gjp2_bcrypt is None:
        return False

    if not await asyncio.to_thread(_check_gjp2, gjp2, credential.gjp2_bcrypt):
        return False

    await ctx.sessions.mark_verified(
        user.id, gjp2, seconds=settings.APP_SESSION_SECONDS
    )

    return True


async def authenticate(
    ctx: AbstractContext, auth: Auth, client: Client
) -> AuthError.OnSuccess[Session]:
    user = await ctx.users.find_by_id(auth.account_id)

    if user is None:
        return AuthError.UNAUTHENTICATED

    if not await _verify(ctx, user, auth.gjp2):
        return AuthError.UNAUTHENTICATED

    if await ctx.bans.find_active(user.id, BanType.ACCOUNT) is not None:
        return AuthError.BANNED

    return Session(user=user, client=client)


async def login(
    ctx: AbstractContext, request: LoginRequest
) -> AuthError.OnSuccess[LoginResult]:
    user = await ctx.users.find_by_username(request.name.strip())

    if user is None:
        return AuthError.INVALID_CREDENTIALS

    if not await _verify(ctx, user, request.gjp2):
        return AuthError.INVALID_CREDENTIALS

    if await ctx.bans.find_active(user.id, BanType.ACCOUNT) is not None:
        return AuthError.ACCOUNT_BANNED

    if request.client.udid:
        await ctx.devices.upsert(user.id, request.client.udid, request.client.platform)

    await ctx.users.touch_last_seen(user.id)
    logger.info("User logged in.", extra={"user_id": user.id})

    return LoginResult(account_id=user.id, user_id=user.id)


def _validate_registration(request: RegisterRequest) -> AuthError.OnSuccess[None]:
    name = request.name.strip()

    if len(name) < _USERNAME_MIN:
        return AuthError.NAME_TOO_SHORT

    if len(name) > _USERNAME_MAX or not _USERNAME_PATTERN.match(name):
        return AuthError.NAME_INVALID

    if len(request.password) < _PASSWORD_MIN:
        return AuthError.PASSWORD_TOO_SHORT

    if len(request.password) > _PASSWORD_MAX:
        return AuthError.PASSWORD_INVALID

    email = request.email.strip()

    if len(email) > _EMAIL_MAX or not _EMAIL_PATTERN.match(email):
        return AuthError.EMAIL_INVALID

    return None


async def register(
    ctx: AbstractContext, request: RegisterRequest, *, ip: str
) -> AuthError.OnSuccess[int]:
    validation = _validate_registration(request)

    if validation is not None:
        return validation

    # A loose limit on attempts stops scripted probing of taken names and
    # emails, while a player struggling to pick a free name is never locked out.
    within_attempts = await ctx.rate_limits.hit(
        "register_attempt",
        ip,
        limit=_REGISTER_ATTEMPT_LIMIT,
        window_seconds=_REGISTER_WINDOW,
    )

    if not within_attempts:
        return AuthError.TOO_MANY_ATTEMPTS

    name = request.name.strip()
    email = request.email.strip().lower()

    if await ctx.users.find_by_username(name) is not None:
        return AuthError.NAME_TAKEN

    if await ctx.users.find_by_email(email) is not None:
        return AuthError.EMAIL_TAKEN

    # Only registrations that create an account count towards the strict limit.
    within_limit = await ctx.rate_limits.hit(
        "register", ip, limit=_REGISTER_LIMIT, window_seconds=_REGISTER_WINDOW
    )

    if not within_limit:
        return AuthError.TOO_MANY_ATTEMPTS

    hashed = await asyncio.to_thread(_hash_gjp2, crypto.gjp2(request.password))
    user_id = await ctx.users.create(name, email)
    await ctx.credentials.upsert(user_id, hashed)
    await ctx.stats.create(user_id)
    default_role = await ctx.roles.find_by_name(_DEFAULT_ROLE)

    if default_role is not None:
        await ctx.roles.assign(
            user_id, default_role.id, granted_by_user_id=None, expires_at=None
        )

    logger.info("User registered.", extra={"user_id": user_id})

    return user_id


async def set_password(
    ctx: AbstractContext, user_id: int, password: str
) -> AuthError.OnSuccess[None]:
    if not _PASSWORD_MIN <= len(password) <= _PASSWORD_MAX:
        return AuthError.PASSWORD_INVALID

    if await ctx.users.find_by_id(user_id) is None:
        return AuthError.USER_NOT_FOUND

    hashed = await asyncio.to_thread(_hash_gjp2, crypto.gjp2(password))
    await ctx.credentials.upsert(user_id, hashed)
    await ctx.sessions.revoke(user_id)
    logger.info("Password set.", extra={"user_id": user_id})

    return None


async def migrate_legacy_password(
    ctx: AbstractContext, username: str, password: str, *, ip: str
) -> AuthError.OnSuccess[int]:
    """Re-hashes an imported 2.1 account's password into a gjp2 hash so the
    2.2 client can log in. The password itself does not change."""

    within_limit = await ctx.rate_limits.hit(
        "migrate", ip, limit=_MIGRATE_LIMIT, window_seconds=_MIGRATE_WINDOW
    )

    if not within_limit:
        return AuthError.TOO_MANY_ATTEMPTS

    user = await ctx.users.find_by_username(username.strip())

    if user is None:
        return AuthError.INVALID_CREDENTIALS

    credential = await ctx.credentials.find_by_user_id(user.id)

    if credential is None:
        return AuthError.INVALID_CREDENTIALS

    gjp2 = crypto.gjp2(password)

    # An account that already holds a gjp2 hash only learns so with the right
    # password, so the page reveals nothing more than the game's login does.
    if credential.legacy_password_bcrypt is None:
        if credential.gjp2_bcrypt is None:
            return AuthError.INVALID_CREDENTIALS

        if not await asyncio.to_thread(_check_gjp2, gjp2, credential.gjp2_bcrypt):
            return AuthError.INVALID_CREDENTIALS

        return AuthError.ALREADY_MIGRATED

    matches = await asyncio.to_thread(
        _check_password, password, credential.legacy_password_bcrypt
    )

    if not matches:
        return AuthError.INVALID_CREDENTIALS

    if await ctx.bans.find_active(user.id, BanType.ACCOUNT) is not None:
        return AuthError.ACCOUNT_BANNED

    hashed = await asyncio.to_thread(_hash_gjp2, gjp2)
    await ctx.credentials.upsert(user.id, hashed)
    await ctx.sessions.revoke(user.id)
    logger.info("Legacy password migrated.", extra={"user_id": user.id})

    return user.id

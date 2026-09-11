from collections.abc import Iterable

WILDCARD = "*"
SEPARATOR = "."
DENY_PREFIX = "-"


def _wildcard_prefixes(permission: str) -> list[str]:
    parts = permission.split(SEPARATOR)

    return [SEPARATOR.join([*parts[:index], WILDCARD]) for index in range(len(parts))]


def matches(granted: str, permission: str) -> bool:
    """Whether the granted string (possibly ending in `*`) covers the permission."""

    if granted in (permission, WILDCARD):
        return True

    if not granted.endswith(SEPARATOR + WILDCARD):
        return False

    prefix = granted.removesuffix(WILDCARD)

    return permission.startswith(prefix)


def is_granted(granted: Iterable[str], permission: str) -> bool:
    """Resolves a permission against a set of grants. A grant prefixed with `-`
    is a deny and wins over every allow that covers the same permission."""

    candidates = {permission, *_wildcard_prefixes(permission)}
    allowed = False

    for entry in granted:
        if entry.startswith(DENY_PREFIX):
            if entry[1:] in candidates:
                return False

            continue

        if entry in candidates:
            allowed = True

    return allowed


def is_valid_name(permission: str) -> bool:
    body = permission.removeprefix(DENY_PREFIX)

    if not body or body == WILDCARD:
        return bool(body)

    parts = body.split(SEPARATOR)

    for index, part in enumerate(parts):
        if part == WILDCARD:
            return index == len(parts) - 1 and index > 0

        if not part or not part.replace("_", "").isalnum():
            return False

    return True

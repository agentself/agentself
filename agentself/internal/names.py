import re
from collections.abc import Iterable

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_WIN_DEVICES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *{f"COM{i}" for i in range(1, 10)},
        *{f"LPT{i}" for i in range(1, 10)},
    }
)

WALLET_KEY_NAME = "wallet.key"
EMAIL_CREDENTIAL_NAME = "email.credential"
EMAIL_ADDRESS_NAME = "email.address"
EMAIL_CONTINUATION_NAME = "internal.email.continuation"
INTERNAL_PREFIX = "internal."
PROTECTED_SECRET_NAMES = frozenset({WALLET_KEY_NAME})


def is_reserved_secret_name(name: str) -> bool:
    return name.casefold().startswith(INTERNAL_PREFIX)


def is_protected_secret_name(name: str, extra: Iterable[str] = ()) -> bool:
    folded = name.casefold()
    for item in (*PROTECTED_SECRET_NAMES, *extra):
        if item.casefold() == folded:
            return True
    return False


def canonical_secret_name(name: str, extra: Iterable[str] = ()) -> str:
    folded = name.casefold()
    for item in PROTECTED_SECRET_NAMES:
        if item.casefold() == folded:
            return item
    for item in extra:
        if item.casefold() == folded:
            return item
    return name


def require_safe_token(value: str, label: str) -> str:
    if not value or not _SAFE.fullmatch(value):
        raise ValueError(f"invalid {label}")
    if value.endswith(".") or value.split(".", 1)[0].upper() in _WIN_DEVICES:
        raise ValueError(f"invalid {label}")
    return value

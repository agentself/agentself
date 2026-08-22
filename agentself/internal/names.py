import re

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

WALLET_KEY_NAME = "wallet.key"
SEND_TOKEN_NAME = "email.send.token"
EMAIL_ADDRESS_NAME = "email.address"
INTERNAL_PREFIX = "internal."
SETUP_PREFIX = "internal.setup."
NOTE_PREFIX = "note."
PROTECTED_HOLD_NAMES = frozenset({WALLET_KEY_NAME})


def require_safe_token(value: str, label: str) -> str:
    if not value or not _SAFE.fullmatch(value):
        raise ValueError(f"invalid {label}")
    return value

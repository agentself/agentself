import re

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def require_safe_token(value: str, label: str) -> str:
    if not value or not _SAFE.fullmatch(value):
        raise ValueError(f"invalid {label}")
    return value

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

_ANSI = re.compile(
    r"(?:\x1b[@-Z\\-_]|[\x80-\x9a\x9c-\x9f]|"
    r"\x1b\[[0-?]*[ -/]*[@-~]|"
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|"
    r"\x1b[PX^_][^\x1b]*\x1b\\)"
)
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def sanitize_text(value: str) -> str:
    """Strip ANSI and other C0/C1 controls. Keep tab, LF, and CR."""

    return _CTRL.sub("", _ANSI.sub("", value))


def sanitize_deep(value: object) -> object:
    """Sanitize strings in nested JSON-like values."""

    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, Mapping):
        return {key: sanitize_deep(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [sanitize_deep(item) for item in value]
    return value

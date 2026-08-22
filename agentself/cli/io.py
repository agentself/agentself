from __future__ import annotations

import sys
from pathlib import Path

from agentself.internal.text import (
    byte_count,
    decode_utf8_text,
    read_text_file,
    sha256_text,
    write_text_file,
)


def read_stdin_text(*, strip_newline: bool = True) -> str:
    buf = getattr(sys.stdin, "buffer", None)
    if buf is not None:
        try:
            raw = buf.read()
        except Exception:
            raw = None
        if isinstance(raw, (bytes, bytearray)):
            return decode_utf8_text(bytes(raw), strip_newline=strip_newline)
    return decode_utf8_text(
        sys.stdin.read().encode("utf-8"), strip_newline=strip_newline
    )


def load_value_file(path: str, *, strip_newline: bool = True) -> str:
    return read_text_file(Path(path), strip_newline=strip_newline)


def store_value_file(path: str, value: str) -> None:
    write_text_file(Path(path), value)


def value_meta(value: str) -> dict[str, object]:
    return {"bytes": byte_count(value), "sha256": sha256_text(value)}

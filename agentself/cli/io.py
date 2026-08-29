from __future__ import annotations

import sys
from pathlib import Path

from agentself.internal.files import atomic_write
from agentself.internal.text import (
    byte_count,
    decode_utf8_text,
    read_text_file,
    sha256_text,
    utf8_bytes,
)


def read_stdin_text(*, strip_newline: bool = True, strip_bom: bool = True) -> str:
    buf = getattr(sys.stdin, "buffer", None)
    if buf is not None:
        try:
            raw = buf.read()
        except Exception:
            raw = None
        if isinstance(raw, (bytes, bytearray)):
            return decode_utf8_text(
                bytes(raw), strip_newline=strip_newline, strip_bom=strip_bom
            )
    return decode_utf8_text(
        sys.stdin.read().encode("utf-8"),
        strip_newline=strip_newline,
        strip_bom=strip_bom,
    )


def load_value_file(
    path: str, *, strip_newline: bool = True, strip_bom: bool = True
) -> str:
    if path == "-":
        return read_stdin_text(strip_newline=strip_newline, strip_bom=strip_bom)
    return read_text_file(Path(path), strip_newline=strip_newline, strip_bom=strip_bom)


def store_value_file(path: str, value: str, *, force: bool = False) -> None:
    dest = Path(path)
    if dest.exists() and not force:
        raise FileExistsError(path)
    atomic_write(dest, utf8_bytes(value), mode=0o600, private_dir=False)


def value_meta(value: str) -> dict[str, object]:
    return {"bytes": byte_count(value), "sha256": sha256_text(value)}

from __future__ import annotations

import hashlib
from pathlib import Path

UTF8_BOM = "\ufeff"
UTF8_BOM_BYTES = b"\xef\xbb\xbf"


def strip_one_trailing_newline(text: str) -> str:
    if text.endswith("\r\n"):
        return text[:-2]
    if text.endswith(("\n", "\r")):
        return text[:-1]
    return text


def decode_utf8_text(
    data: bytes, *, strip_newline: bool = True, strip_bom: bool = True
) -> str:
    """Decode UTF-8 bytes. A leading BOM is removed; CRLF inside the value is kept."""

    raw = data.removeprefix(UTF8_BOM_BYTES) if strip_bom else data
    text = raw.decode("utf-8")
    if strip_newline:
        return strip_one_trailing_newline(text)
    return text


def utf8_bytes(text: str) -> bytes:
    return text.encode("utf-8")


def byte_count(text: str) -> int:
    return len(utf8_bytes(text))


def sha256_text(text: str) -> str:
    return hashlib.sha256(utf8_bytes(text)).hexdigest()


def read_text_file(
    path: Path, *, strip_newline: bool = True, strip_bom: bool = True
) -> str:
    return decode_utf8_text(
        Path(path).read_bytes(), strip_newline=strip_newline, strip_bom=strip_bom
    )


def write_text_file(path: Path, text: str) -> None:
    Path(path).write_bytes(utf8_bytes(text))

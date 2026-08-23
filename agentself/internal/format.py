from __future__ import annotations

import json
from pathlib import Path

CURRENT_FORMAT_VERSION = 1


def load_json_file(path: Path) -> object:
    """UTF-8 JSON. A leading BOM (Windows Notepad) is ignored."""

    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def read_format_version(data: dict) -> int:
    """Declared format version. A missing key is an error, never CURRENT."""

    if "format_version" not in data:
        raise ValueError("format_version is missing")
    raw = data["format_version"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError("format_version is not an integer")
    return raw


def format_version_error(filename: str, data: dict) -> str | None:
    """Unsupported format_version message, or None if this CLI can read `data`.

    Missing ``format_version`` fails closed. Do not include file contents.
    """

    try:
        version = read_format_version(data)
    except ValueError as exc:
        return f"cannot read {filename}: {exc}"
    if version == CURRENT_FORMAT_VERSION:
        return None
    if version > CURRENT_FORMAT_VERSION:
        return (
            f"cannot read {filename}: format_version {version} is newer than this CLI; "
            "upgrade agentself"
        )
    return f"cannot read {filename}: format_version {version} is unsupported"

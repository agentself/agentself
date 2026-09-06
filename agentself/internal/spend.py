from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from agentself.internal.files import (
    atomic_write_text,
    ensure_private_dir,
    exclusive,
    identity_home,
)
from agentself.internal.format import (
    CURRENT_FORMAT_VERSION,
    format_version_error,
    load_json_file,
)
from agentself.internal.names import require_safe_token

LIMIT_NAME = "limit.json"
_HEX_TO = re.compile(r"^0x[0-9a-fA-F]+$")


class LimitError(Exception):
    """Malformed or unsupported spend limit. Never includes a secret."""


class LimitExists(Exception):
    """A limit file is already present and --force was not set."""


@dataclass(frozen=True)
class AssetRule:
    max: Decimal | None = None
    reserve: Decimal | None = None


@dataclass(frozen=True)
class SpendLimit:
    max: Decimal | None = None
    reserve: Decimal | None = None
    to: tuple[str, ...] = ()
    assets: dict[str, AssetRule] | None = None

    def row_for(self, asset: str) -> AssetRule | None:
        if self.assets is None:
            return AssetRule(max=self.max, reserve=self.reserve)
        row = self.assets.get(asset)
        if row is None:
            return None
        return AssetRule(
            max=row.max if row.max is not None else self.max,
            reserve=row.reserve if row.reserve is not None else self.reserve,
        )


def canonical_amount(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def parse_decimal(value: object) -> Decimal | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = Decimal(value.strip())
    except (InvalidOperation, ValueError, ArithmeticError):
        return None
    if not parsed.is_finite():
        return None
    return parsed


def remaining_of(balance: Decimal, reserve: Decimal) -> Decimal:
    left = balance - reserve
    return left if left > 0 else Decimal("0")


def destination_allowed(to: str, allowed: tuple[str, ...]) -> bool:
    wanted = to.strip()
    for item in allowed:
        other = item.strip()
        if not other:
            continue
        if _HEX_TO.fullmatch(wanted) and _HEX_TO.fullmatch(other):
            if wanted.lower() == other.lower():
                return True
        elif wanted == other:
            return True
    return False


def parse_limit(data: object) -> SpendLimit:
    if not isinstance(data, dict):
        raise LimitError("cannot read spend limit")
    err = format_version_error(LIMIT_NAME, data)
    if err:
        raise LimitError(err)
    max_amount = _optional_amount(data.get("max"), "max")
    reserve = _optional_amount(data.get("reserve"), "reserve")
    destinations = _optional_to(data.get("to"))
    assets = _optional_assets(data.get("assets"))
    return SpendLimit(max=max_amount, reserve=reserve, to=destinations, assets=assets)


def limit_payload(limit: SpendLimit) -> dict[str, object]:
    payload: dict[str, object] = {"format_version": CURRENT_FORMAT_VERSION}
    if limit.max is not None:
        payload["max"] = canonical_amount(limit.max)
    if limit.reserve is not None:
        payload["reserve"] = canonical_amount(limit.reserve)
    if limit.to:
        payload["to"] = list(limit.to)
    if limit.assets is not None:
        assets: dict[str, dict[str, str]] = {}
        for name, rule in limit.assets.items():
            row: dict[str, str] = {}
            if rule.max is not None:
                row["max"] = canonical_amount(rule.max)
            if rule.reserve is not None:
                row["reserve"] = canonical_amount(rule.reserve)
            assets[name] = row
        payload["assets"] = assets
    return payload


def public_limit(limit: SpendLimit) -> dict[str, object]:
    payload: dict[str, object] = {"limit": True}
    stored = limit_payload(limit)
    stored.pop("format_version", None)
    payload.update(stored)
    return payload


class LimitStorage:
    """Identity-local spend limit. Missing file is unlimited."""

    def __init__(self, vault_root: Path) -> None:
        self._root = Path(vault_root)

    def load(self, identity_id: str) -> SpendLimit | None:
        path = self._path(identity_id)
        with exclusive(self._root):
            self._safe_home(identity_id, create=False)
            if path.is_symlink():
                raise OSError("unsafe spend limit path")
            if not path.is_file():
                return None
            try:
                data = load_json_file(path)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise LimitError("cannot read spend limit") from exc
            return parse_limit(data)

    def save(self, identity_id: str, limit: SpendLimit, *, force: bool) -> None:
        text = json.dumps(limit_payload(limit), separators=(",", ":")) + "\n"
        with exclusive(self._root):
            folder = self._safe_home(identity_id, create=True)
            dest = folder / LIMIT_NAME
            if dest.is_symlink() or (dest.exists() and not dest.is_file()):
                raise OSError("unsafe spend limit path")
            if dest.is_file() and not force:
                raise LimitExists
            atomic_write_text(dest, text, mode=0o600)

    def _path(self, identity_id: str) -> Path:
        identity = require_safe_token(identity_id, "identity id")
        return identity_home(self._root, identity) / LIMIT_NAME

    def _safe_home(self, identity_id: str, *, create: bool) -> Path:
        identity = require_safe_token(identity_id, "identity id")
        folder = identity_home(self._root, identity)
        if folder.is_symlink():
            raise OSError("unsafe spend limit path")
        if create:
            ensure_private_dir(folder)
        elif folder.exists() and not folder.is_dir():
            raise OSError("unsafe spend limit path")
        return folder


def _optional_amount(value: object, field: str) -> Decimal | None:
    if value is None:
        return None
    parsed = parse_decimal(value)
    if parsed is None or parsed < 0:
        raise LimitError(f"invalid {field}")
    return parsed


def _optional_to(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise LimitError("invalid to")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise LimitError("invalid to")
        names.append(item.strip())
    return tuple(names)


def _optional_assets(value: object) -> dict[str, AssetRule] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise LimitError("invalid assets")
    assets: dict[str, AssetRule] = {}
    for raw_name, raw_rule in value.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise LimitError("invalid assets")
        try:
            name = require_safe_token(raw_name.strip(), "asset")
        except ValueError as exc:
            raise LimitError("invalid assets") from exc
        if not isinstance(raw_rule, dict):
            raise LimitError("invalid assets")
        assets[name] = AssetRule(
            max=_optional_amount(raw_rule.get("max"), "max"),
            reserve=_optional_amount(raw_rule.get("reserve"), "reserve"),
        )
    return assets

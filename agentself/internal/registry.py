from __future__ import annotations

import json
from pathlib import Path

from agentself.internal.files import (
    IdentityBusy,
    atomic_write_text,
    ensure_private_dir,
    exclusive,
)
from agentself.internal.format import (
    CURRENT_FORMAT_VERSION,
    format_version_error,
)
from agentself.internal.log import Log
from agentself.internal.names import require_safe_token
from agentself.internal.types import Identity


class RegistryError(Exception):
    """registry.json exists but is unreadable. Fail closed."""

    def __init__(self, message: str = "cannot read registry.json") -> None:
        super().__init__(message)


class RegistryFormatError(RegistryError):
    """Unsupported format_version. Fail closed. Do not rewrite."""


class FileIdentityAccess:
    """Not CRUD."""

    def __init__(
        self,
        vault_root: Path,
        log: Log,
        *,
        allowed_bindings: frozenset[str] | None = None,
    ) -> None:
        self._root = Path(vault_root)
        self._registry = self._root / "registry.json"
        self._log = log
        # Test fallback; production compose injects CHANNELS["store"].names.
        self._allowed = (
            frozenset(allowed_bindings)
            if allowed_bindings is not None
            else frozenset(("sops", "pass"))
        )

    def find(self, identity_id: str) -> Identity | None:
        require_safe_token(identity_id, "identity id")
        records = self._load()
        raw = records.get(identity_id)
        if raw is None:
            self._log.record("find", identity_id, None, "miss")
            return None
        self._log.record("find", identity_id, None, "ok")
        return _identity(raw, self._allowed)

    def init(self, identity_id: str, recipient: str, store_binding: str) -> Identity:
        require_safe_token(identity_id, "identity id")
        if not recipient or not recipient.startswith("age1"):
            self._log.record("init", identity_id, None, "refused")
            raise ValueError("invalid recipient")
        if store_binding not in self._allowed:
            self._log.record("init", identity_id, None, "refused")
            raise ValueError("invalid store binding")
        try:
            with exclusive(self._root):
                records = self._load()
                if identity_id in records:
                    self._log.record("init", identity_id, None, "exists")
                    return _identity(records[identity_id], self._allowed)
                identity = Identity(
                    id=identity_id,
                    recipient=recipient,
                    store_binding=store_binding,
                )
                records[identity_id] = {
                    "id": identity.id,
                    "recipient": identity.recipient,
                    "store_binding": identity.store_binding,
                }
                self._save(records)
                self._log.record("init", identity_id, None, "ok")
                return identity
        except IdentityBusy as exc:
            raise RegistryError("identity directory busy") from exc

    def add_wallet_material_name(self, identity_id: str, name: str) -> Identity:
        """Record provider-declared wallet material without binding a wallet."""

        require_safe_token(identity_id, "identity id")
        try:
            require_safe_token(name, "wallet material name")
        except ValueError:
            raise RegistryError("invalid wallet material name") from None
        try:
            with exclusive(self._root):
                records = self._load()
                raw = records.get(identity_id)
                if raw is None:
                    raise RegistryError("identity not found")
                identity = _identity(raw, self._allowed)
                if name in identity.wallet_material_names:
                    return identity
                raw["wallet_material_names"] = [
                    *identity.wallet_material_names,
                    name,
                ]
                self._save(records)
                return _identity(raw, self._allowed)
        except IdentityBusy as exc:
            raise RegistryError("identity directory busy") from exc

    def _load(self) -> dict[str, dict[str, object]]:
        if not self._registry.exists():
            return {}
        try:
            data = json.loads(self._registry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError("cannot read registry.json") from exc
        if not isinstance(data, dict):
            raise RegistryError("cannot read registry.json")
        err = format_version_error("registry.json", data)
        if err:
            raise RegistryFormatError(err)
        identities = data.get("identities", {})
        if not isinstance(identities, dict):
            raise RegistryError("cannot read registry.json")
        out: dict[str, dict[str, object]] = {}
        for pid, raw in identities.items():
            if not isinstance(pid, str):
                raise RegistryError("cannot read registry.json")
            identity = _identity(raw, self._allowed)
            if identity.id != pid:
                raise RegistryError("cannot read registry.json")
            out[pid] = {
                "id": identity.id,
                "recipient": identity.recipient,
                "store_binding": identity.store_binding,
            }
            if identity.wallet_material_names:
                out[pid]["wallet_material_names"] = list(identity.wallet_material_names)
        return out

    def _save(self, records: dict[str, dict[str, object]]) -> None:
        ensure_private_dir(self._root)
        payload = (
            json.dumps(
                {"format_version": CURRENT_FORMAT_VERSION, "identities": records},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        atomic_write_text(self._registry, payload)


def _identity(raw: object, allowed: frozenset[str]) -> Identity:
    if not isinstance(raw, dict):
        raise RegistryError("cannot read registry.json")
    pid = raw.get("id")
    recipient = raw.get("recipient")
    store_binding = raw.get("store_binding")
    raw_names = raw.get("wallet_material_names", [])
    legacy_name = raw.get("wallet_material_name")
    if (
        not isinstance(pid, str)
        or not isinstance(recipient, str)
        or not isinstance(store_binding, str)
    ):
        raise RegistryError("cannot read registry.json")
    try:
        require_safe_token(pid, "identity id")
    except ValueError:
        raise RegistryError("cannot read registry.json") from None
    if not _public_recipient(recipient):
        raise RegistryError("cannot read registry.json")
    if store_binding not in allowed:
        raise RegistryError("cannot read registry.json")
    if not isinstance(raw_names, list) or any(
        not isinstance(name, str) for name in raw_names
    ):
        raise RegistryError("cannot read registry.json")
    names = list(raw_names)
    if legacy_name is not None:
        if not isinstance(legacy_name, str):
            raise RegistryError("cannot read registry.json")
        names.insert(0, legacy_name)
    validated_names: list[str] = []
    for name in names:
        try:
            require_safe_token(name, "wallet material name")
        except ValueError:
            raise RegistryError("cannot read registry.json") from None
        if name not in validated_names:
            validated_names.append(name)
    return Identity(
        id=pid,
        recipient=recipient,
        store_binding=store_binding,
        wallet_material_names=tuple(validated_names),
    )


def _public_recipient(value: str) -> bool:
    if not value.startswith("age1"):
        return False
    if "AGE-SECRET-KEY" in value:
        return False
    if any(ch.isspace() for ch in value):
        return False
    return 8 <= len(value) <= 128

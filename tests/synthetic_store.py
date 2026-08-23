from __future__ import annotations

from agentself.backends.store.contract import (
    HostTool,
    SecretExists,
    SecretMissing,
    StoreAccess,
)
from agentself.internal.names import require_safe_token


class MemoryStoreAccess(StoreAccess):
    """In-memory secrets. No gpg/sops/age for CRUD. Not a shipped bind."""

    def __init__(self, data: dict[tuple[str, str], str] | None = None) -> None:
        self._data = data if data is not None else {}
        self.prepare_calls = 0

    def prepare(self, identity_id: str) -> None:
        require_safe_token(identity_id, "identity id")
        self.prepare_calls += 1

    def required_tools(self) -> tuple[HostTool, ...]:
        return ()

    def create(self, identity_id: str, name: str, value: str) -> None:
        require_safe_token(identity_id, "identity id")
        require_safe_token(name, "name")
        key = (identity_id, name)
        if key in self._data:
            raise SecretExists(name)
        self._data[key] = value

    def get(self, identity_id: str, name: str) -> str:
        require_safe_token(identity_id, "identity id")
        require_safe_token(name, "name")
        try:
            return self._data[(identity_id, name)]
        except KeyError:
            raise SecretMissing(name) from None

    def update(self, identity_id: str, name: str, value: str) -> None:
        require_safe_token(identity_id, "identity id")
        require_safe_token(name, "name")
        key = (identity_id, name)
        if key not in self._data:
            raise SecretMissing(name)
        self._data[key] = value

    def list(self, identity_id: str) -> list[str]:
        require_safe_token(identity_id, "identity id")
        return sorted(name for pid, name in self._data if pid == identity_id)

    def delete(self, identity_id: str, name: str) -> None:
        require_safe_token(identity_id, "identity id")
        require_safe_token(name, "name")
        try:
            del self._data[(identity_id, name)]
        except KeyError:
            raise SecretMissing(name) from None

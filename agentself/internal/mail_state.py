from __future__ import annotations

import hashlib
import os
from pathlib import Path

from agentself.internal.files import (
    atomic_write_text,
    exclusive,
    identity_home,
)


class ActedMailState:
    """Backend-neutral local task state for provider message IDs."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def set(self, identity_id: str, message_id: str, acted: bool) -> None:
        marker = self._marker(identity_id, message_id)
        with exclusive(self._root):
            if acted:
                atomic_write_text(marker, message_id)
                return
            try:
                os.unlink(marker)
            except FileNotFoundError:
                return

    def apply(
        self, identity_id: str, messages: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        with exclusive(self._root):
            for message in messages:
                message_id = str(message.get("id") or "")
                message["acted"] = bool(message_id) and self._is_acted(
                    identity_id, message_id
                )
        return messages

    def _is_acted(self, identity_id: str, message_id: str) -> bool:
        marker = self._marker(identity_id, message_id)
        try:
            return marker.read_text(encoding="utf-8") == message_id
        except FileNotFoundError:
            return False

    def _marker(self, identity_id: str, message_id: str) -> Path:
        digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()
        return identity_home(self._root, identity_id) / "email" / "acted" / digest

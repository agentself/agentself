from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from agentself.internal.files import (
    atomic_write_text,
    exclusive,
    identity_home,
)
from agentself.internal.names import require_safe_token
from agentself.internal.types import MailboxMessage

MAIL_REF_PATTERN = r"m[1-9][0-9]{0,11}"
_MAIL_REF_RE = re.compile(rf"^{MAIL_REF_PATTERN}$")


class MailRefCollision(Exception):
    """A compact ref already names a different provider message."""


def is_mail_ref(value: str) -> bool:
    return _MAIL_REF_RE.fullmatch(value) is not None


class MailRefState:
    """Persistent backend-neutral compact refs for provider message IDs."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def apply(
        self, identity_id: str, messages: list[MailboxMessage]
    ) -> list[MailboxMessage]:
        with exclusive(self._root):
            for message in messages:
                message_id = str(message.get("id") or "")
                if message_id:
                    message["ref"] = self._remember_locked(identity_id, message_id)
        return messages

    def remember(self, identity_id: str, message_id: str) -> str:
        with exclusive(self._root):
            return self._remember_locked(identity_id, message_id)

    def resolve(self, identity_id: str, value: str) -> str:
        if not is_mail_ref(value):
            return value
        with exclusive(self._root):
            path = self._path(identity_id, value)
            if path.is_symlink():
                raise OSError("linked mail ref")
            try:
                message_id = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                raise KeyError(value) from None
            if not _valid_message_id(message_id):
                raise OSError("invalid mail ref mapping")
            return message_id

    def known_provider_id(self, identity_id: str, message_id: str) -> bool:
        if is_mail_ref(message_id) or not _valid_message_id(message_id):
            return False
        with exclusive(self._root):
            folder = _state_dir(self._root, identity_id, "refs")
            if not folder.exists():
                return False
            for path in folder.iterdir():
                if not is_mail_ref(path.name):
                    continue
                if path.is_symlink() or not path.is_file():
                    raise OSError("unsafe mail ref mapping")
                existing = path.read_text(encoding="utf-8")
                if not _valid_message_id(existing):
                    raise OSError("invalid mail ref mapping")
                if existing == message_id:
                    return True
            return False

    def _remember_locked(self, identity_id: str, message_id: str) -> str:
        if not _valid_message_id(message_id):
            raise ValueError("invalid provider message id")
        folder = _state_dir(self._root, identity_id, "refs")
        matches: list[str] = []
        highest = 0
        if folder.exists():
            for path in folder.iterdir():
                if not is_mail_ref(path.name):
                    continue
                if path.is_symlink() or not path.is_file():
                    raise OSError("unsafe mail ref mapping")
                existing = path.read_text(encoding="utf-8")
                if not _valid_message_id(existing):
                    raise OSError("invalid mail ref mapping")
                highest = max(highest, int(path.name[1:]))
                if existing == message_id:
                    matches.append(path.name)
        if len(matches) > 1:
            raise MailRefCollision(message_id)
        if matches:
            return matches[0]
        ref = f"m{highest + 1}"
        if not is_mail_ref(ref):
            raise OSError("mail ref space exhausted")
        path = self._path(identity_id, ref)
        if path.exists() or path.is_symlink():
            raise MailRefCollision(ref)
        atomic_write_text(path, message_id)
        return ref

    def _path(self, identity_id: str, ref: str) -> Path:
        if not is_mail_ref(ref):
            raise ValueError("invalid mail ref")
        return _state_dir(self._root, identity_id, "refs") / ref


def _valid_message_id(value: str) -> bool:
    return (
        bool(value)
        and len(value.encode("utf-8")) <= 4096
        and all(ord(char) >= 32 for char in value)
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
        self, identity_id: str, messages: list[MailboxMessage]
    ) -> list[MailboxMessage]:
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
        return _state_dir(self._root, identity_id, "acted") / digest


def _state_dir(root: Path, identity_id: str, name: str) -> Path:
    identity = require_safe_token(identity_id, "identity id")
    home = identity_home(root, identity)
    email = home / "email"
    folder = email / name
    for path in (home, email, folder):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise OSError("unsafe mail state directory")
    return folder

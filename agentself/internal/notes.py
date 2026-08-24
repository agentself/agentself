from __future__ import annotations

import os
from pathlib import Path

from agentself.internal.files import (
    atomic_write,
    ensure_private_dir,
    exclusive,
    identity_home,
)
from agentself.internal.names import require_safe_token


class NoteMissing(Exception):
    pass


class NoteStorage:
    """Identity-local, non-secret UTF-8 notes."""

    def __init__(self, vault_root: Path) -> None:
        self._root = Path(vault_root)

    def set(self, identity_id: str, name: str, value: str) -> str:
        path = self._path(identity_id, name)
        data = value.encode("utf-8")
        with exclusive(self._root):
            folder = self._safe_home(identity_id, create=True)
            path = folder / name
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise OSError("unsafe note path")
            if path.is_file() and path.read_bytes() == data:
                self._private_file(path)
                return "unchanged"
            status = "updated" if path.is_file() else "created"
            atomic_write(path, data, mode=0o600)
            return status

    def get(self, identity_id: str, name: str) -> str:
        path = self._path(identity_id, name)
        with exclusive(self._root):
            self._safe_home(identity_id, create=False)
            if path.is_symlink() or not path.is_file():
                raise NoteMissing(name)
            return path.read_bytes().decode("utf-8")

    def list(self, identity_id: str) -> list[str]:
        with exclusive(self._root):
            folder = self._safe_home(identity_id, create=False)
            if not folder.exists():
                return []
            names: list[str] = []
            for path in folder.iterdir():
                try:
                    require_safe_token(path.name, "note name")
                except ValueError:
                    continue
                if path.is_file() and not path.is_symlink():
                    names.append(path.name)
            return sorted(names)

    def exists(self, identity_id: str, name: str) -> bool:
        path = self._path(identity_id, name)
        with exclusive(self._root):
            self._safe_home(identity_id, create=False)
            return path.is_file() and not path.is_symlink()

    def delete(self, identity_id: str, name: str) -> None:
        path = self._path(identity_id, name)
        with exclusive(self._root):
            self._safe_home(identity_id, create=False)
            if path.is_symlink() or not path.is_file():
                raise NoteMissing(name)
            path.unlink()

    def _path(self, identity_id: str, name: str) -> Path:
        identity = require_safe_token(identity_id, "identity id")
        safe_name = require_safe_token(name, "note name")
        return identity_home(self._root, identity) / "notes" / safe_name

    def _safe_home(self, identity_id: str, *, create: bool) -> Path:
        identity = require_safe_token(identity_id, "identity id")
        parent = identity_home(self._root, identity)
        folder = parent / "notes"
        if parent.is_symlink() or folder.is_symlink():
            raise OSError("unsafe notes directory")
        if create:
            ensure_private_dir(folder)
        elif folder.exists() and not folder.is_dir():
            raise OSError("unsafe notes directory")
        return folder

    @staticmethod
    def _private_file(path: Path) -> None:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

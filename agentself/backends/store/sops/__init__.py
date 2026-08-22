from __future__ import annotations

import os
import tempfile
from pathlib import Path

from agentself.backends.store.contract import (
    HoldNameExists,
    HoldNameMissing,
    StoreAccess,
    StoreResourceError,
)
from agentself.backends.store.run import run_cmd
from agentself.internal.files import (
    VaultBusy,
    atomic_write,
    ensure_private_dir,
    exclusive,
    identity_home,
    secrets_home,
    shred_unlink,
)
from agentself.internal.log import Log
from agentself.internal.names import require_safe_token


class SopsStoreAccess(StoreAccess):
    def __init__(self, vault_root: Path, log: Log) -> None:
        self._root = Path(vault_root)
        self._log = log

    def seal(self, principal_id: str, name: str, value: str) -> None:
        try:
            with exclusive(self._root):
                path = self._entry(principal_id, name)
                ensure_private_dir(path.parent)
                _scrub_plain_tmps(path.parent)
                if _filled(path):
                    try:
                        existing = self._decrypt(principal_id, path)
                    except StoreResourceError:
                        self._log.record("store_seal", principal_id, name, "exists")
                        raise HoldNameExists(name) from None
                    if existing == value:
                        self._log.record("store_seal", principal_id, name, "ok")
                        return
                    self._log.record("store_seal", principal_id, name, "exists")
                    raise HoldNameExists(name)
                self._encrypt(principal_id, path, value)
                self._log.record("store_seal", principal_id, name, "ok")
        except VaultBusy as exc:
            raise StoreResourceError("store timeout") from exc
        except OSError as exc:
            raise StoreResourceError("seal failed") from exc

    def reveal(self, principal_id: str, name: str) -> str:
        path = self._entry(principal_id, name)
        if not _filled(path):
            self._log.record("store_reveal", principal_id, name, "missing")
            raise HoldNameMissing(name)
        value = self._decrypt(principal_id, path)
        self._log.record("store_reveal", principal_id, name, "ok")
        return value

    def replace(self, principal_id: str, name: str, value: str) -> None:
        try:
            with exclusive(self._root):
                path = self._entry(principal_id, name)
                _scrub_plain_tmps(path.parent)
                if not _filled(path):
                    self._log.record("store_replace", principal_id, name, "missing")
                    raise HoldNameMissing(name)
                self._encrypt(principal_id, path, value)
                self._log.record("store_replace", principal_id, name, "ok")
        except VaultBusy as exc:
            raise StoreResourceError("store timeout") from exc
        except OSError as exc:
            raise StoreResourceError("seal failed") from exc

    def list(self, principal_id: str) -> list[str]:
        require_safe_token(principal_id, "principal id")
        hold = ensure_private_dir(self._hold_dir(principal_id))
        try:
            with exclusive(self._root):
                _scrub_plain_tmps(hold)
        except VaultBusy:
            pass
        names = sorted(
            path.stem
            for path in hold.iterdir()
            if path.is_file() and path.suffix == ".sops" and _filled(path)
        )
        self._log.record("store_list", principal_id, None, "ok")
        return names

    def delete(self, principal_id: str, name: str) -> None:
        try:
            with exclusive(self._root):
                path = self._entry(principal_id, name)
                _scrub_plain_tmps(path.parent)
                if not _filled(path):
                    self._log.record("store_delete", principal_id, name, "missing")
                    raise HoldNameMissing(name)
                shred_unlink(path)
                self._log.record("store_delete", principal_id, name, "ok")
        except VaultBusy as exc:
            raise StoreResourceError("store timeout") from exc
        except OSError as exc:
            raise StoreResourceError("delete failed") from exc

    def _hold_dir(self, principal_id: str) -> Path:
        return secrets_home(self._root, principal_id)

    def _entry(self, principal_id: str, name: str) -> Path:
        require_safe_token(principal_id, "principal id")
        require_safe_token(name, "name")
        return self._hold_dir(principal_id) / f"{name}.sops"

    def _key_file(self, principal_id: str) -> Path:
        return identity_home(self._root, principal_id) / "agent.agekey"

    def _recipient(self, principal_id: str) -> str:
        key = self._key_file(principal_id)
        proc = run_cmd(["age-keygen", "-y", str(key)])
        if proc.returncode != 0:
            raise StoreResourceError("recipient unavailable")
        recipient = proc.stdout.decode("utf-8").strip()
        if not recipient.startswith("age1"):
            raise StoreResourceError("recipient unavailable")
        return recipient

    def _encrypt(self, principal_id: str, path: Path, value: str) -> None:
        recipient = self._recipient(principal_id)
        fd, tmp_name = tempfile.mkstemp(prefix="seal.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(value.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o600)
            proc = run_cmd(
                [
                    "sops",
                    "--encrypt",
                    "--age",
                    recipient,
                    "--input-type",
                    "binary",
                    "--output-type",
                    "binary",
                    tmp_name,
                ],
            )
            if proc.returncode != 0 or not proc.stdout:
                raise StoreResourceError("seal failed")
            atomic_write(path, proc.stdout)
        finally:
            shred_unlink(tmp_name)

    def _decrypt(self, principal_id: str, path: Path) -> str:
        key = self._key_file(principal_id)
        env = os.environ.copy()
        env.pop("SOPS_AGE_KEY", None)
        env.pop("AGE_SECRET_KEY", None)
        env["SOPS_AGE_KEY_FILE"] = str(key)
        proc = run_cmd(
            [
                "sops",
                "--decrypt",
                "--input-type",
                "binary",
                "--output-type",
                "binary",
                str(path),
            ],
            env=env,
        )
        if proc.returncode != 0:
            raise StoreResourceError("reveal failed")
        try:
            return proc.stdout.decode("utf-8")
        except UnicodeDecodeError:
            raise StoreResourceError("reveal failed") from None


def _scrub_plain_tmps(hold: Path) -> None:
    try:
        items = list(hold.iterdir())
    except OSError:
        return
    for path in items:
        name = path.name
        if name.startswith("seal.") and name.endswith(".tmp"):
            shred_unlink(path)


def _filled(path: Path) -> bool:
    try:
        if path.is_symlink():
            return False
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False

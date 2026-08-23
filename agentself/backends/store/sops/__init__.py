from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agentself.backends.store.contract import (
    HostTool,
    SecretExists,
    SecretMissing,
    StoreAccess,
    StoreResourceError,
)
from agentself.backends.store.run import run_cmd
from agentself.internal.files import (
    IdentityBusy,
    atomic_write,
    ensure_private_dir,
    exclusive,
    identity_home,
    secrets_home,
    shred_unlink,
)
from agentself.internal.log import Log
from agentself.internal.names import require_safe_token
from agentself.internal.store_tools import store_required_tools

_SOPS_FOREIGN_KEYS = (
    "SOPS_AGE_KEY",
    "AGE_SECRET_KEY",
    "SOPS_AGE_KEY_FILE",
    "SOPS_AGE_RECIPIENTS",
    "SOPS_PGP_FP",
    "SOPS_KMS_ARN",
    "SOPS_GCP_KMS_IDS",
    "SOPS_AZURE_KEYVAULT_URL",
    "SOPS_VAULT_URIS",
    "SOPS_HUAWEICLOUD_KMS_IDS",
)


def _sops_env(*, key_file: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for name in _SOPS_FOREIGN_KEYS:
        env.pop(name, None)
    if key_file is not None:
        env["SOPS_AGE_KEY_FILE"] = str(key_file)
    return env


class SopsStoreAccess(StoreAccess):
    def __init__(self, vault_root: Path, log: Log) -> None:
        self._root = Path(vault_root)
        self._log = log

    def required_tools(self) -> tuple[HostTool, ...]:
        return store_required_tools("sops")

    @contextmanager
    def _locked(self, os_fail: str) -> Iterator[None]:
        try:
            with exclusive(self._root):
                yield
        except IdentityBusy as exc:
            raise StoreResourceError("store timeout") from exc
        except OSError as exc:
            raise StoreResourceError(os_fail) from exc

    def create(self, identity_id: str, name: str, value: str) -> None:
        with self._locked("create failed"):
            path = self._entry(identity_id, name)
            ensure_private_dir(path.parent)
            _scrub_plain_tmps(path.parent)
            if _filled(path):
                self._log.record("store_create", identity_id, name, "exists")
                raise SecretExists(name)
            self._encrypt(identity_id, path, value)
            self._log.record("store_create", identity_id, name, "ok")

    def get(self, identity_id: str, name: str) -> str:
        path = self._entry(identity_id, name)
        if not _filled(path):
            self._log.record("store_get", identity_id, name, "missing")
            raise SecretMissing(name)
        value = self._decrypt(identity_id, path)
        self._log.record("store_get", identity_id, name, "ok")
        return value

    def update(self, identity_id: str, name: str, value: str) -> None:
        with self._locked("create failed"):
            path = self._entry(identity_id, name)
            _scrub_plain_tmps(path.parent)
            if not _filled(path):
                self._log.record("store_update", identity_id, name, "missing")
                raise SecretMissing(name)
            self._encrypt(identity_id, path, value)
            self._log.record("store_update", identity_id, name, "ok")

    def list(self, identity_id: str) -> list[str]:
        require_safe_token(identity_id, "identity id")
        secrets_dir = ensure_private_dir(self._secrets_dir(identity_id))
        try:
            with exclusive(self._root):
                _scrub_plain_tmps(secrets_dir)
        except IdentityBusy:
            pass
        names = sorted(
            path.stem
            for path in secrets_dir.iterdir()
            if path.suffix == ".sops" and _filled(path)
        )
        self._log.record("store_list", identity_id, None, "ok")
        return names

    def delete(self, identity_id: str, name: str) -> None:
        with self._locked("delete failed"):
            path = self._entry(identity_id, name)
            _scrub_plain_tmps(path.parent)
            if not _filled(path):
                self._log.record("store_delete", identity_id, name, "missing")
                raise SecretMissing(name)
            shred_unlink(path)
            self._log.record("store_delete", identity_id, name, "ok")

    def _secrets_dir(self, identity_id: str) -> Path:
        return secrets_home(self._root, identity_id)

    def _entry(self, identity_id: str, name: str) -> Path:
        require_safe_token(identity_id, "identity id")
        require_safe_token(name, "name")
        return self._secrets_dir(identity_id) / f"{name}.sops"

    def _key_file(self, identity_id: str) -> Path:
        return identity_home(self._root, identity_id) / "agent.agekey"

    def _recipient(self, identity_id: str) -> str:
        key = self._key_file(identity_id)
        proc = run_cmd(["age-keygen", "-y", str(key)])
        if proc.returncode != 0:
            raise StoreResourceError("recipient unavailable")
        recipient = proc.stdout.decode("utf-8").strip()
        if not recipient.startswith("age1"):
            raise StoreResourceError("recipient unavailable")
        return recipient

    def _encrypt(self, identity_id: str, path: Path, value: str) -> None:
        recipient = self._recipient(identity_id)
        fd, tmp_name = tempfile.mkstemp(
            prefix="secret.", suffix=".tmp", dir=path.parent
        )
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
                env=_sops_env(),
            )
            if proc.returncode != 0 or not proc.stdout:
                raise StoreResourceError("create failed")
            atomic_write(path, proc.stdout)
        finally:
            shred_unlink(tmp_name)

    def _decrypt(self, identity_id: str, path: Path) -> str:
        key = self._key_file(identity_id)
        env = _sops_env(key_file=key)
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
            raise StoreResourceError("get failed")
        try:
            return proc.stdout.decode("utf-8")
        except UnicodeDecodeError:
            raise StoreResourceError("get failed") from None


def _scrub_plain_tmps(secrets_dir: Path) -> None:
    try:
        items = list(secrets_dir.iterdir())
    except OSError:
        return
    for path in items:
        name = path.name
        if name.startswith("secret.") and name.endswith(".tmp"):
            shred_unlink(path)


def _filled(path: Path) -> bool:
    try:
        if path.is_symlink():
            return False
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False

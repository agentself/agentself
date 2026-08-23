from __future__ import annotations

from pathlib import Path

from agentself.backends.store.contract import (
    SecretExists,
    SecretMissing,
    StoreAccess,
    StoreResourceError,
)
from agentself.backends.store.run import run_cmd
from agentself.internal.files import IdentityBusy, exclusive, identity_home
from agentself.internal.gpg import gpg_fingerprint, pass_argv, pass_env
from agentself.internal.log import Log
from agentself.internal.names import require_safe_token


class PassStoreAccess(StoreAccess):
    """Own GPG key and PASSWORD_STORE_DIR."""

    def __init__(self, vault_root: Path, log: Log) -> None:
        self._root = Path(vault_root)
        self._log = log

    def create(self, identity_id: str, name: str, value: str) -> None:
        require_safe_token(name, "name")
        try:
            with exclusive(self._root):
                self._ensure_store(identity_id)
                if self._entry(identity_id, name).exists():
                    self._log.record("store_create", identity_id, name, "exists")
                    raise SecretExists(name)
                self._insert(identity_id, name, value, force=False)
                self._log.record("store_create", identity_id, name, "ok")
        except IdentityBusy as exc:
            raise StoreResourceError("store timeout") from exc

    def get(self, identity_id: str, name: str) -> str:
        require_safe_token(name, "name")
        self._ensure_store(identity_id)
        if not self._entry(identity_id, name).exists():
            self._log.record("store_get", identity_id, name, "missing")
            raise SecretMissing(name)
        env = self._env(identity_id)
        proc = run_cmd(pass_argv(["pass", "show", "--", name]), env=env)
        if proc.returncode != 0:
            self._log.record("store_get", identity_id, name, "missing")
            raise SecretMissing(name)
        self._log.record("store_get", identity_id, name, "ok")
        try:
            text = proc.stdout.decode("utf-8")
        except UnicodeDecodeError:
            raise StoreResourceError("get failed") from None
        return text.removesuffix("\n")

    def update(self, identity_id: str, name: str, value: str) -> None:
        require_safe_token(name, "name")
        try:
            with exclusive(self._root):
                self._ensure_store(identity_id)
                if not self._entry(identity_id, name).exists():
                    self._log.record("store_update", identity_id, name, "missing")
                    raise SecretMissing(name)
                self._insert(identity_id, name, value, force=True)
                self._log.record("store_update", identity_id, name, "ok")
        except IdentityBusy as exc:
            raise StoreResourceError("store timeout") from exc

    def list(self, identity_id: str) -> list[str]:
        self._ensure_store(identity_id)
        store = self._store_dir(identity_id)
        names = sorted(
            str(path.relative_to(store).with_suffix("")) for path in store.rglob("*.gpg")
        )
        self._log.record("store_list", identity_id, None, "ok")
        return names

    def delete(self, identity_id: str, name: str) -> None:
        require_safe_token(name, "name")
        try:
            with exclusive(self._root):
                self._ensure_store(identity_id)
                if not self._entry(identity_id, name).exists():
                    self._log.record("store_delete", identity_id, name, "missing")
                    raise SecretMissing(name)
                env = self._env(identity_id)
                proc = run_cmd(
                    pass_argv(["pass", "rm", "--force", "--", name]), env=env
                )
                if proc.returncode != 0 or self._entry(identity_id, name).exists():
                    path = self._entry(identity_id, name)
                    try:
                        path.unlink()
                    except OSError as exc:
                        raise StoreResourceError("delete failed") from exc
                    if path.exists():
                        raise StoreResourceError("delete failed")
                self._log.record("store_delete", identity_id, name, "ok")
        except IdentityBusy as exc:
            raise StoreResourceError("store timeout") from exc

    def _base(self, identity_id: str) -> Path:
        require_safe_token(identity_id, "identity id")
        return identity_home(self._root, identity_id)

    def _gpg_home(self, identity_id: str) -> Path:
        return self._base(identity_id) / "gnupg"

    def _store_dir(self, identity_id: str) -> Path:
        return self._base(identity_id) / "password-store"

    def _entry(self, identity_id: str, name: str) -> Path:
        return self._store_dir(identity_id) / f"{name}.gpg"

    def _env(self, identity_id: str) -> dict[str, str]:
        return pass_env(self._gpg_home(identity_id), self._store_dir(identity_id))

    def _ensure_store(self, identity_id: str) -> None:
        store = self._store_dir(identity_id)
        if (store / ".gpg-id").exists():
            return
        gpg_home = self._gpg_home(identity_id)
        if not gpg_home.is_dir():
            raise StoreResourceError("pass store missing")
        fingerprint = self._fingerprint(identity_id)
        store.mkdir(mode=0o700, parents=True, exist_ok=True)
        proc = run_cmd(
            pass_argv(["pass", "init", "--", fingerprint]), env=self._env(identity_id)
        )
        if proc.returncode != 0:
            raise StoreResourceError("pass store missing")

    def _fingerprint(self, identity_id: str) -> str:
        env = self._env(identity_id)
        proc = run_cmd(
            ["gpg", "--list-secret-keys", "--with-colons"],
            env=env,
        )
        if proc.returncode != 0:
            raise StoreResourceError("pass key missing")
        fpr = gpg_fingerprint(proc.stdout.decode("utf-8"))
        if fpr is None:
            raise StoreResourceError("pass key missing")
        return fpr

    def _insert(self, identity_id: str, name: str, value: str, *, force: bool) -> None:
        argv = ["pass", "insert", "-m"]
        if force:
            argv.append("-f")
        argv.extend(["--", name])
        payload = value if value.endswith("\n") else value + "\n"
        proc = run_cmd(
            pass_argv(argv),
            env=self._env(identity_id),
            stdin=payload.encode("utf-8"),
        )
        if proc.returncode != 0:
            raise StoreResourceError("create failed")

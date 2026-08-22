from __future__ import annotations

import os
from pathlib import Path

from agentself.backends.store.contract import (
    HoldNameExists,
    HoldNameMissing,
    StoreAccess,
    StoreResourceError,
)
from agentself.backends.store.run import run_cmd
from agentself.internal.files import VaultBusy, exclusive, identity_home
from agentself.internal.gpg import bindable_home
from agentself.internal.log import Log
from agentself.internal.names import require_safe_token


class PassStoreAccess(StoreAccess):
    """Own GPG key and PASSWORD_STORE_DIR."""

    def __init__(self, vault_root: Path, log: Log) -> None:
        self._root = Path(vault_root)
        self._log = log

    def seal(self, principal_id: str, name: str, value: str) -> None:
        require_safe_token(name, "name")
        try:
            with exclusive(self._root):
                self._ensure_hold(principal_id)
                if self._entry(principal_id, name).exists():
                    env = self._env(principal_id)
                    proc = run_cmd(["pass", "show", "--", name], env=env)
                    if proc.returncode == 0:
                        existing = proc.stdout.decode("utf-8").removesuffix("\n")
                        if existing == value:
                            self._log.record("store_seal", principal_id, name, "ok")
                            return
                    self._log.record("store_seal", principal_id, name, "exists")
                    raise HoldNameExists(name)
                self._insert(principal_id, name, value, force=False)
                self._log.record("store_seal", principal_id, name, "ok")
        except VaultBusy as exc:
            raise StoreResourceError("store timeout") from exc

    def reveal(self, principal_id: str, name: str) -> str:
        require_safe_token(name, "name")
        self._ensure_hold(principal_id)
        if not self._entry(principal_id, name).exists():
            self._log.record("store_reveal", principal_id, name, "missing")
            raise HoldNameMissing(name)
        env = self._env(principal_id)
        proc = run_cmd(["pass", "show", "--", name], env=env)
        if proc.returncode != 0:
            self._log.record("store_reveal", principal_id, name, "missing")
            raise HoldNameMissing(name)
        self._log.record("store_reveal", principal_id, name, "ok")
        try:
            text = proc.stdout.decode("utf-8")
        except UnicodeDecodeError:
            raise StoreResourceError("reveal failed") from None
        return text.removesuffix("\n")

    def replace(self, principal_id: str, name: str, value: str) -> None:
        require_safe_token(name, "name")
        try:
            with exclusive(self._root):
                self._ensure_hold(principal_id)
                if not self._entry(principal_id, name).exists():
                    self._log.record("store_replace", principal_id, name, "missing")
                    raise HoldNameMissing(name)
                self._insert(principal_id, name, value, force=True)
                self._log.record("store_replace", principal_id, name, "ok")
        except VaultBusy as exc:
            raise StoreResourceError("store timeout") from exc

    def list(self, principal_id: str) -> list[str]:
        self._ensure_hold(principal_id)
        store = self._store_dir(principal_id)
        names: list[str] = []
        for path in store.rglob("*.gpg"):
            rel = path.relative_to(store)
            names.append(str(rel.with_suffix("")))
        names.sort()
        self._log.record("store_list", principal_id, None, "ok")
        return names

    def delete(self, principal_id: str, name: str) -> None:
        require_safe_token(name, "name")
        try:
            with exclusive(self._root):
                self._ensure_hold(principal_id)
                if not self._entry(principal_id, name).exists():
                    self._log.record("store_delete", principal_id, name, "missing")
                    raise HoldNameMissing(name)
                env = self._env(principal_id)
                proc = run_cmd(["pass", "rm", "--force", "--", name], env=env)
                if proc.returncode != 0 or self._entry(principal_id, name).exists():
                    path = self._entry(principal_id, name)
                    try:
                        path.unlink()
                    except OSError as exc:
                        raise StoreResourceError("delete failed") from exc
                    if path.exists():
                        raise StoreResourceError("delete failed")
                self._log.record("store_delete", principal_id, name, "ok")
        except VaultBusy as exc:
            raise StoreResourceError("store timeout") from exc

    def _base(self, principal_id: str) -> Path:
        require_safe_token(principal_id, "principal id")
        return identity_home(self._root, principal_id)

    def _gpg_home(self, principal_id: str) -> Path:
        return self._base(principal_id) / "gnupg"

    def _store_dir(self, principal_id: str) -> Path:
        return self._base(principal_id) / "password-store"

    def _entry(self, principal_id: str, name: str) -> Path:
        return self._store_dir(principal_id) / f"{name}.gpg"

    def _env(self, principal_id: str) -> dict[str, str]:
        env = os.environ.copy()
        env["GNUPGHOME"] = str(bindable_home(self._gpg_home(principal_id)))
        env["PASSWORD_STORE_DIR"] = str(self._store_dir(principal_id))
        env["PASSWORD_STORE_GPG_OPTS"] = "--pinentry-mode loopback --batch"
        env["GPG_TTY"] = ""
        return env

    def _ensure_hold(self, principal_id: str) -> None:
        store = self._store_dir(principal_id)
        if (store / ".gpg-id").exists():
            return
        gpg_home = self._gpg_home(principal_id)
        if not gpg_home.is_dir():
            raise StoreResourceError("pass hold missing")
        fingerprint = self._fingerprint(principal_id)
        store.mkdir(mode=0o700, parents=True, exist_ok=True)
        proc = run_cmd(["pass", "init", "--", fingerprint], env=self._env(principal_id))
        if proc.returncode != 0:
            raise StoreResourceError("pass hold missing")

    def _fingerprint(self, principal_id: str) -> str:
        env = self._env(principal_id)
        proc = run_cmd(
            ["gpg", "--list-secret-keys", "--with-colons"],
            env=env,
        )
        if proc.returncode != 0:
            raise StoreResourceError("pass key missing")
        for line in proc.stdout.decode("utf-8").splitlines():
            parts = line.split(":")
            if parts and parts[0] == "fpr" and len(parts) > 9:
                fpr = parts[9]
                if len(fpr) >= 40 and all(ch in "0123456789abcdefABCDEF" for ch in fpr):
                    return fpr
        raise StoreResourceError("pass key missing")

    def _insert(self, principal_id: str, name: str, value: str, *, force: bool) -> None:
        argv = ["pass", "insert", "-m"]
        if force:
            argv.append("-f")
        argv.extend(["--", name])
        payload = value if value.endswith("\n") else value + "\n"
        proc = run_cmd(
            argv,
            env=self._env(principal_id),
            stdin=payload.encode("utf-8"),
        )
        if proc.returncode != 0:
            raise StoreResourceError("seal failed")

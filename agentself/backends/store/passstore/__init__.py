from __future__ import annotations

import re
import subprocess
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
    atomic_write_text,
    ensure_private_dir,
    exclusive,
    identity_home,
    resolve_tool,
    shred_unlink,
)
from agentself.internal.gpg import (
    gpg_fingerprint as parse_fingerprint,
)
from agentself.internal.gpg import (
    pass_argv,
    pass_env,
)
from agentself.internal.log import Log
from agentself.internal.names import require_safe_token

_SECRET = re.compile(r"AGE-SECRET-KEY-[A-Za-z0-9-]+")


class PassStoreAccess(StoreAccess):
    """Own GPG key and PASSWORD_STORE_DIR."""

    def __init__(self, vault_root: Path, log: Log) -> None:
        self._root = Path(vault_root)
        self._log = log

    def required_tools(self) -> tuple[HostTool, ...]:
        return (HostTool("gpg"), HostTool("pass"))

    def prepare(self, identity_id: str) -> None:
        require_safe_token(identity_id, "identity id")
        pdir = identity_home(self._root, identity_id)
        batch = pdir / ".gpg-batch"
        try:
            with exclusive(self._root):
                shred_unlink(batch)
                try:
                    missing = [
                        tool.name
                        for tool in self.required_tools()
                        if not _have_tool(tool.name)
                    ]
                    if missing:
                        raise StoreResourceError(" and ".join(missing) + " not on PATH")
                    gnupg = ensure_private_dir(pdir / "gnupg")
                    store_dir = pdir / "password-store"
                    env = pass_env(gnupg, store_dir)
                    if not _gpg_has_secret(env):
                        _generate_principal_gpg(pdir, identity_id, env)
                    fingerprint = _gpg_fingerprint(env)
                    if not (store_dir / ".gpg-id").is_file():
                        _pass_init(env, store_dir, fingerprint)
                finally:
                    shred_unlink(batch)
        except IdentityBusy as exc:
            raise StoreResourceError("store timeout") from exc

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
            str(path.relative_to(store).with_suffix(""))
            for path in store.rglob("*.gpg")
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
        fpr = parse_fingerprint(proc.stdout.decode("utf-8"))
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


def _have_tool(name: str) -> bool:
    path = Path(resolve_tool(name))
    if len(path.parts) < 2:
        return False
    try:
        return path.is_file()
    except OSError:
        return False


def _run_host(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 30,
    failed: str = "keygen failed",
) -> subprocess.CompletedProcess[bytes]:
    cmd = [resolve_tool(argv[0]), *argv[1:]]
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError:
        raise StoreResourceError(f"{argv[0] if argv else 'tool'} not on PATH") from None
    except subprocess.TimeoutExpired:
        raise StoreResourceError(failed) from None


def _host_failure_message(prefix: str, proc: subprocess.CompletedProcess[bytes]) -> str:
    """One line. Never a secret value."""

    raw = (proc.stderr or b"") + b"\n" + (proc.stdout or b"")
    text = raw.decode("utf-8", "replace")
    detail = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if lower.startswith("gpg: generating") or lower in ("gpg: done",):
            continue
        if stripped.startswith("%"):
            continue
        for head in ("gpg-agent:", "gpg:"):
            if stripped.startswith(head):
                stripped = stripped[len(head) :].strip()
                break
        else:
            stripped = _strip_gpg_agent_prefix(stripped)
        detail = stripped
    msg = f"{prefix}: {detail}" if detail else prefix
    return _redact(" ".join(msg.split()))


def _redact(text: str) -> str:
    return _SECRET.sub("AGE-SECRET-KEY-[redacted]", text)


def _strip_gpg_agent_prefix(line: str) -> str:
    if line.startswith("gpg-agent[") and ":" in line:
        return line.split(":", 1)[1].strip()
    return line


def _gpg_has_secret(env: dict[str, str]) -> bool:
    proc = _run_host(
        [
            "gpg",
            "--homedir",
            env["GNUPGHOME"],
            "--batch",
            "--list-secret-keys",
            "--with-colons",
        ],
        env=env,
        failed="gpg keygen failed",
    )
    if proc.returncode != 0:
        return False
    try:
        text = proc.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return any(line.startswith("sec:") for line in text.splitlines())


def _generate_principal_gpg(pdir: Path, identity_id: str, env: dict[str, str]) -> None:
    batch = pdir / ".gpg-batch"
    body = "\n".join(
        [
            "%echo generating identity GPG key",
            "%no-protection",
            "Key-Type: EDDSA",
            "Key-Curve: Ed25519",
            "Subkey-Type: ECDH",
            "Subkey-Curve: Curve25519",
            f"Name-Real: agent-{identity_id}",
            f"Name-Email: {identity_id}@agentself.local",
            "Expire-Date: 0",
            "%commit",
            "%echo done",
            "",
        ]
    )
    try:
        atomic_write_text(batch, body)
        proc = _run_host(
            [
                "gpg",
                "--homedir",
                env["GNUPGHOME"],
                "--batch",
                "--pinentry-mode",
                "loopback",
                "--generate-key",
                str(batch),
            ],
            env=env,
            timeout=60,
            failed="gpg keygen failed",
        )
        if proc.returncode != 0:
            raise StoreResourceError(_host_failure_message("gpg keygen failed", proc))
    finally:
        shred_unlink(batch)


def _gpg_fingerprint(env: dict[str, str]) -> str:
    proc = _run_host(
        [
            "gpg",
            "--homedir",
            env["GNUPGHOME"],
            "--batch",
            "--list-secret-keys",
            "--with-colons",
        ],
        env=env,
        failed="failed to read GPG fingerprint",
    )
    if proc.returncode != 0:
        raise StoreResourceError(
            _host_failure_message("failed to read GPG fingerprint", proc)
        )
    try:
        text = proc.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise StoreResourceError("failed to read GPG fingerprint") from None
    fpr = parse_fingerprint(text)
    if fpr is None:
        raise StoreResourceError("failed to read GPG fingerprint")
    return fpr


def _pass_init(env: dict[str, str], store_dir: Path, fingerprint: str) -> None:
    ensure_private_dir(store_dir)
    proc = _run_host(
        pass_argv(["pass", "init", fingerprint]),
        env=env,
        timeout=60,
        failed="pass init failed",
    )
    if proc.returncode != 0 or not (store_dir / ".gpg-id").is_file():
        raise StoreResourceError(_host_failure_message("pass init failed", proc))

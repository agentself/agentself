from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from agentself.bind import bind_from_env, public_recipient
from agentself.host import (
    ENV_AGE_KEY_FILE,
    ENV_IDENTITY_DIR,
    ENV_IDENTITY_ID,
    ENV_MAIL_DOMAIN,
)
from agentself.internal.custody.errors import HostToolMissing, UnboundCaller
from agentself.internal.files import (
    VaultBusy,
    atomic_write_text,
    ensure_private_dir,
    exclusive,
    identity_home,
    resolve_tool,
    shred_unlink,
)
from agentself.internal.format import (
    CURRENT_FORMAT_VERSION,
    format_version_error,
)
from agentself.internal.gpg import bindable_home, pass_argv
from agentself.internal.names import require_safe_token
from agentself.internal.types import BoundCaller

DEFAULT_IDENTITY = "agent"
CONFIG_NAME = "config.json"
DEFAULT_VAULT_NAME = ".agentself"
_SECRET = re.compile(r"AGE-SECRET-KEY-[A-Za-z0-9-]+")
_HEXKEY = re.compile(r"(?i)(?<![0-9a-f])0x[0-9a-f]{64}(?![0-9a-f])")


class VaultStateError(Exception):
    """config.json exists but is not a usable identity file. Fail closed."""

    def __init__(self, message: str = "cannot read config.json") -> None:
        super().__init__(message)


def default_vault() -> Path:
    override = os.environ.get(ENV_IDENTITY_DIR, "").strip()
    if override:
        return Path(override)
    return Path.home() / DEFAULT_VAULT_NAME


def config_path(vault: Path) -> Path:
    return Path(vault) / CONFIG_NAME


def load_config(vault: Path) -> dict[str, str]:
    return _read_config(Path(vault))


def save_config(vault: Path, data: dict[str, str]) -> None:
    root = Path(vault)
    try:
        with exclusive(root):
            _read_config(root)
            _write_config(root, data)
    except VaultBusy as exc:
        raise VaultStateError("vault busy") from exc


def require_supported_formats(vault: Path) -> None:
    """Read-only format_version check. Missing files are OK. Does not write."""

    root = Path(vault)
    _read_config(root)
    path = root / "registry.json"
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    err = format_version_error("registry.json", data)
    if err:
        raise VaultStateError(err)


def merge_config(vault: Path, updates: dict[str, str]) -> dict[str, str]:
    root = Path(vault)
    try:
        with exclusive(root):
            cfg = _read_config(root)
            cfg.update({key: value for key, value in updates.items() if value})
            _write_config(root, cfg)
            return cfg
    except VaultBusy as exc:
        raise VaultStateError("vault busy") from exc


def resolve_setting(
    vault: Path,
    key: str,
    env_name: str,
    default: str = "",
    explicit: str | None = None,
) -> str:
    """Flag, then env, then config.json. Empty string is unset."""

    if explicit is not None and explicit.strip():
        return explicit.strip()
    env = os.environ.get(env_name, "").strip()
    if env:
        return env
    stored = load_config(vault).get(key, "").strip()
    if stored:
        return stored
    return default


def mail_domain(vault: Path, explicit: str | None = None) -> str:
    return resolve_setting(
        vault, "mail_domain", ENV_MAIL_DOMAIN, default="", explicit=explicit
    )


def resolve_age_key_file(vault: Path, stored: str) -> str:
    if not stored:
        return ""
    path = Path(stored)
    root = Path(vault)
    if not path.is_absolute():
        path = root / path
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            return ""
    if path.name.startswith("-"):
        return ""
    return str(path)


def bind_local(vault: Path) -> BoundCaller:
    """Env first (tests and escape hatches), then the local identity file."""

    try:
        return bind_from_env()
    except UnboundCaller:
        pass
    cfg = load_config(vault)
    principal_id = (
        os.environ.get(ENV_IDENTITY_ID, "").strip()
        or cfg.get("identity_id", "").strip()
    )
    key_file = os.environ.get(ENV_AGE_KEY_FILE, "").strip() or resolve_age_key_file(
        vault, cfg.get("age_key_file", "")
    )
    if not principal_id or not key_file:
        raise UnboundCaller("unbound caller")
    return BoundCaller(principal_id, public_recipient(key_file))


def ensure_age_key(vault: Path, principal_id: str, store: str = "sops") -> Path:
    """Host keygen. Not a Manager call. Private key stays in the file."""

    require_safe_token(principal_id, "principal id")
    root = Path(vault)
    try:
        with exclusive(root):
            if store == "pass":
                return _ensure_pass_principal(root, principal_id)
            return _ensure_age_keygen(root, principal_id)
    except VaultBusy as exc:
        raise VaultStateError("vault busy") from exc


def redact_secrets(text: str) -> str:
    text = _SECRET.sub("AGE-SECRET-KEY-[redacted]", text)
    return _HEXKEY.sub("0x[redacted]", text)


def format_status(view: dict[str, object], vault: Path) -> str:
    raw_email = view.get("email")
    raw_wallet = view.get("wallet")
    email: dict[str, object] = raw_email if isinstance(raw_email, dict) else {}
    wallet: dict[str, object] = raw_wallet if isinstance(raw_wallet, dict) else {}
    addr = str(wallet.get("address") or "")
    recipient = str(view.get("recipient") or "")
    if email.get("owned_address") and email.get("address"):
        email_line = str(email["address"])
        nxt = ""
    else:
        email_line = "not configured"
        nxt = "next: agentself email connect\n"
    wallet_backend = str(view.get("wallet_backend") or "")
    email_backend = str(view.get("email_backend") or "")
    return redact_secrets(
        f"identity_dir: {vault}\n"
        f"wallet: {addr}\n"
        f"wallet_backend: {wallet_backend}\n"
        f"recipient: {recipient}\n"
        f"email_backend: {email_backend}\n"
        f"email: {email_line}\n"
        f"{nxt}"
    )


def _ensure_age_keygen(vault: Path, principal_id: str) -> Path:
    root = Path(vault)
    pdir = ensure_private_dir(identity_home(root, principal_id))
    key = pdir / "agent.agekey"
    if key.is_symlink():
        raise VaultStateError("age key file is not usable")
    if key.is_file() and key.stat().st_size == 0:
        shred_unlink(key)
    if not key.is_file():
        try:
            proc = subprocess.run(
                [resolve_tool("age-keygen"), "-o", str(key)],
                capture_output=True,
                check=False,
                timeout=30,
            )
        except FileNotFoundError:
            shred_unlink(key)
            raise FileNotFoundError("age not on PATH") from None
        except subprocess.TimeoutExpired:
            shred_unlink(key)
            raise RuntimeError("keygen failed") from None
        if proc.returncode != 0 or not key.is_file() or key.is_symlink():
            shred_unlink(key)
            raise RuntimeError("keygen failed")
    try:
        os.chmod(key, 0o600)
    except OSError:
        pass
    return key


def _ensure_pass_principal(vault: Path, principal_id: str) -> Path:
    pdir = identity_home(Path(vault), principal_id)
    batch = pdir / ".gpg-batch"
    shred_unlink(batch)
    try:
        _require_pass_tools()
        key = _ensure_age_keygen(vault, principal_id)
        gnupg = ensure_private_dir(pdir / "gnupg")
        store_dir = pdir / "password-store"
        env = _pass_env(gnupg, store_dir)
        if not _gpg_has_secret(env, gnupg):
            _generate_principal_gpg(pdir, principal_id, env, gnupg)
        fingerprint = _gpg_fingerprint(env, gnupg)
        if not (store_dir / ".gpg-id").is_file():
            _pass_init(env, store_dir, fingerprint)
        return key
    finally:
        shred_unlink(batch)


def _require_pass_tools() -> None:
    missing = [name for name in ("gpg", "pass") if not _have_tool(name)]
    if missing:
        raise HostToolMissing(" and ".join(missing))


def _have_tool(name: str) -> bool:
    path = Path(resolve_tool(name))
    if len(path.parts) < 2:
        return False
    try:
        return path.is_file()
    except OSError:
        return False


def _pass_env(gnupg: Path, store_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["GNUPGHOME"] = str(bindable_home(gnupg))
    env["PASSWORD_STORE_DIR"] = str(store_dir)
    env["PASSWORD_STORE_GPG_OPTS"] = "--pinentry-mode loopback --batch"
    env["GPG_TTY"] = ""
    if os.name == "nt":
        env.setdefault("NoDefaultCurrentDirectoryInExePath", "1")
    return env


def _run_host(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 30,
    failed: str = "keygen failed",
) -> subprocess.CompletedProcess[bytes]:
    cmd = [resolve_tool(str(argv[0])), *[str(part) for part in argv[1:]]]
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError:
        raise HostToolMissing(str(argv[0]) if argv else "tool") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(failed) from None


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
    return redact_secrets(" ".join(msg.split()))


def _strip_gpg_agent_prefix(line: str) -> str:
    if line.startswith("gpg-agent[") and ":" in line:
        return line.split(":", 1)[1].strip()
    return line


def _raise_host_failed(prefix: str, proc: subprocess.CompletedProcess[bytes]) -> None:
    raise RuntimeError(_host_failure_message(prefix, proc))


def _gpg_has_secret(env: dict[str, str], gnupg: Path) -> bool:
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


def _generate_principal_gpg(
    pdir: Path, principal_id: str, env: dict[str, str], gnupg: Path
) -> None:
    batch = pdir / ".gpg-batch"
    body = "\n".join(
        [
            "%echo generating principal GPG key",
            "%no-protection",
            "Key-Type: EDDSA",
            "Key-Curve: Ed25519",
            "Subkey-Type: ECDH",
            "Subkey-Curve: Curve25519",
            f"Name-Real: agent-{principal_id}",
            f"Name-Email: {principal_id}@agentself.local",
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
            _raise_host_failed("gpg keygen failed", proc)
    finally:
        shred_unlink(batch)


def _gpg_fingerprint(env: dict[str, str], gnupg: Path) -> str:
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
        _raise_host_failed("failed to read GPG fingerprint", proc)
    try:
        text = proc.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise RuntimeError("failed to read GPG fingerprint") from None
    for line in text.splitlines():
        parts = line.split(":")
        if parts and parts[0] == "fpr" and len(parts) > 9:
            fpr = parts[9]
            if len(fpr) >= 40 and all(ch in "0123456789abcdefABCDEF" for ch in fpr):
                return fpr
    raise RuntimeError("failed to read GPG fingerprint")


def _pass_init(env: dict[str, str], store_dir: Path, fingerprint: str) -> None:
    ensure_private_dir(store_dir)
    proc = _run_host(
        pass_argv(["pass", "init", fingerprint]),
        env=env,
        timeout=60,
        failed="pass init failed",
    )
    if proc.returncode != 0 or not (store_dir / ".gpg-id").is_file():
        _raise_host_failed("pass init failed", proc)


def _read_config(vault: Path) -> dict[str, str]:
    path = config_path(vault)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VaultStateError("cannot read config.json") from exc
    if not isinstance(data, dict):
        raise VaultStateError("cannot read config.json")
    err = format_version_error("config.json", data)
    if err:
        raise VaultStateError(err)
    out: dict[str, str] = {}
    for key, value in data.items():
        if key == "format_version":
            continue
        if isinstance(key, str) and isinstance(value, str):
            out[key] = value
    return out


def _write_config(vault: Path, data: dict[str, str]) -> None:
    ensure_private_dir(vault)
    payload: dict[str, object] = {"format_version": CURRENT_FORMAT_VERSION}
    for key, value in data.items():
        if key == "format_version":
            continue
        if isinstance(key, str) and isinstance(value, str):
            payload[key] = value
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    atomic_write_text(config_path(vault), text)

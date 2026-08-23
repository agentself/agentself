from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agentself.bind import bind_from_env, public_recipient
from agentself.host import (
    ENV_AGE_KEY_FILE,
    ENV_IDENTITY_DIR,
    ENV_IDENTITY_ID,
    ENV_MAIL_DOMAIN,
)
from agentself.internal.custody.errors import UnboundCaller
from agentself.internal.files import (
    IdentityBusy,
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
    load_json_file,
)
from agentself.internal.names import require_safe_token
from agentself.internal.types import BoundCaller

DEFAULT_IDENTITY = "agent"
CONFIG_NAME = "config.json"
DEFAULT_DIR_NAME = ".agentself"
_SECRET = re.compile(r"AGE-SECRET-KEY-[A-Za-z0-9-]+")
_HEXKEY = re.compile(r"(?i)(?<![0-9a-f])0x[0-9a-f]{64}(?![0-9a-f])")


class IdentityStateError(Exception):
    """config.json exists but is not a usable identity file. Fail closed."""

    def __init__(self, message: str = "cannot read config.json") -> None:
        super().__init__(message)


def default_identity_dir() -> Path:
    override = os.environ.get(ENV_IDENTITY_DIR, "").strip()
    return Path(override) if override else Path.home() / DEFAULT_DIR_NAME


def config_path(vault: Path) -> Path:
    return Path(vault) / CONFIG_NAME


def load_config(vault: Path) -> dict[str, str]:
    return _read_config(Path(vault))


def save_config(vault: Path, data: dict[str, str]) -> None:
    with _locked_vault(vault) as root:
        _read_config(root)
        _write_config(root, data)


def require_supported_formats(vault: Path) -> None:
    """Read-only format_version check. Missing files are OK. Does not write."""

    root = Path(vault)
    _read_config(root)
    path = root / "registry.json"
    if not path.is_file():
        return
    try:
        data = load_json_file(path)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    err = format_version_error("registry.json", data)
    if err:
        raise IdentityStateError(err)


def merge_config(vault: Path, updates: dict[str, str]) -> dict[str, str]:
    with _locked_vault(vault) as root:
        cfg = _read_config(root)
        cfg.update((key, value) for key, value in updates.items() if value)
        _write_config(root, cfg)
        return cfg


def resolve_setting(
    vault: Path,
    key: str,
    env_name: str,
    default: str = "",
    explicit: str | None = None,
) -> str:
    """Flag, then env, then config.json. Empty string is unset."""

    if explicit is not None:
        value = explicit.strip()
        if value:
            return value
    env = os.environ.get(env_name, "").strip()
    if env:
        return env
    return load_config(vault).get(key, "").strip() or default


def mail_domain(vault: Path, explicit: str | None = None) -> str:
    return resolve_setting(
        vault, "mail_domain", ENV_MAIL_DOMAIN, default="", explicit=explicit
    )


def resolve_age_key_file(vault: Path, stored: str) -> str:
    if not stored:
        return ""
    path = Path(stored)
    if path.name.startswith("-"):
        return ""
    root = Path(vault)
    if path.is_absolute():
        return str(path)
    path = root / path
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return ""
    return str(path)


def bind_local(vault: Path) -> BoundCaller:
    """Env first (tests and escape hatches), then the local identity file."""

    try:
        return bind_from_env()
    except UnboundCaller:
        pass
    cfg = load_config(vault)
    identity_id = (
        os.environ.get(ENV_IDENTITY_ID, "").strip()
        or cfg.get("identity_id", "").strip()
    )
    key_file = os.environ.get(ENV_AGE_KEY_FILE, "").strip() or resolve_age_key_file(
        vault, cfg.get("age_key_file", "")
    )
    if not identity_id or not key_file:
        raise UnboundCaller("not initialized")
    return BoundCaller(identity_id, public_recipient(key_file))


def ensure_age_key(vault: Path, identity_id: str) -> Path:
    """Host keygen. Not a Manager call. Private key stays in the file."""

    require_safe_token(identity_id, "identity id")
    with _locked_vault(vault) as root:
        return _ensure_age_keygen(root, identity_id)


def redact_secrets(text: str) -> str:
    text = _SECRET.sub("AGE-SECRET-KEY-[redacted]", text)
    return _HEXKEY.sub("0x[redacted]", text)


def format_status(view: dict[str, object], vault: Path) -> str:
    raw_email = view.get("email")
    raw_wallet = view.get("wallet")
    email = raw_email if isinstance(raw_email, dict) else {}
    wallet = raw_wallet if isinstance(raw_wallet, dict) else {}
    addr = str(wallet.get("address") or "")
    recipient = str(view.get("recipient") or "")
    owned = email.get("owned_address") and email.get("address")
    email_line = str(email["address"]) if owned else "not configured"
    nxt = "" if owned else "next: agentself email connect\n"
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


@contextmanager
def _locked_vault(vault: Path) -> Iterator[Path]:
    root = Path(vault)
    try:
        with exclusive(root):
            yield root
    except IdentityBusy as exc:
        raise IdentityStateError("identity directory busy") from exc


def _ensure_age_keygen(vault: Path, identity_id: str) -> Path:
    pdir = ensure_private_dir(identity_home(vault, identity_id))
    key = pdir / "agent.agekey"
    if key.is_symlink():
        raise IdentityStateError("age key file is not usable")
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


def _read_config(vault: Path) -> dict[str, str]:
    path = config_path(vault)
    if not path.is_file():
        return {}
    try:
        data = load_json_file(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise IdentityStateError("cannot read config.json") from exc
    if not isinstance(data, dict):
        raise IdentityStateError("cannot read config.json")
    err = format_version_error("config.json", data)
    if err:
        raise IdentityStateError(err)
    return {
        key: value
        for key, value in data.items()
        if key != "format_version" and isinstance(value, str)
    }


def _write_config(vault: Path, data: dict[str, str]) -> None:
    ensure_private_dir(vault)
    payload: dict[str, object] = {
        "format_version": CURRENT_FORMAT_VERSION,
        **{key: value for key, value in data.items() if key != "format_version"},
    }
    atomic_write_text(
        config_path(vault), json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )

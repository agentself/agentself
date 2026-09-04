from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from agentself.cli.io import (
    ValueFileRefused,
    load_value_file,
    store_value_file,
    value_meta,
)
from agentself.cli.outcomes import CliOutcome, CliRaw, CliSuccess
from agentself.cli.runtime import (
    client,
    fail,
    resource_name_error,
    secret_from_args,
    secret_value_error,
)
from agentself.internal.custody.errors import ProtectedName, Refused
from agentself.internal.files import host_env
from agentself.internal.names import WALLET_KEY_NAME
from agentself.local import redact_secrets

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RUN_HELP = "agentself secret run --help"


def create_secret(args, vault: Path) -> CliOutcome:
    name = (getattr(args, "name", None) or "").strip()
    if name:
        invalid = resource_name_error(
            args, args.name, "secret", "agentself secret create --help"
        )
        if invalid is not None:
            return invalid
    if _secret_bulk_requested(args):
        return _secret_create_bulk(vault, args)
    if not (getattr(args, "name", None) or "").strip():
        return fail(
            args,
            2,
            "refused",
            "need a name",
            nxt="agentself secret create --help",
        )
    value, err = secret_from_args(args)
    if err is not None:
        return secret_value_error(args, err)
    assert value is not None
    if args.name == WALLET_KEY_NAME and not getattr(args, "unsafe", False):
        return fail(
            args,
            2,
            "refused",
            f"{WALLET_KEY_NAME} is protected",
            nxt="agentself secret create NAME --unsafe",
        )
    access = client(vault)
    try:
        unchanged = access.create(args.name, value)
    except Refused:
        return fail(
            args,
            2,
            "refused",
            nxt="agentself secret update NAME",
        )
    if unchanged:
        return fail(
            args,
            2,
            "refused",
            nxt="agentself secret update NAME",
        )
    payload: dict[str, object] = {"name": args.name}
    return CliSuccess(payload)


def get_secret(args, vault: Path) -> CliOutcome:
    invalid = resource_name_error(
        args, args.name, "secret", "agentself secret get --help"
    )
    if invalid is not None:
        return invalid
    access = client(vault)
    name = args.name
    path = (args.to_file or "").strip()
    protected_names = frozenset(access.protected_secret_names())
    as_raw = bool(getattr(args, "as_raw", False))
    if name in protected_names and not args.unsafe and not args.meta:
        return fail(
            args,
            2,
            "refused",
            f"{name} is protected",
            nxt="agentself secret get NAME --unsafe",
        )
    value = access.get(name)
    meta = value_meta(value)
    if args.meta:
        return CliSuccess({"name": name, **meta, "protected": name in protected_names})
    if path:
        try:
            store_value_file(path, value, force=bool(getattr(args, "force", False)))
        except ValueFileRefused as exc:
            nxt = "agentself secret get NAME --file PATH"
            if exc.reason == "file exists":
                nxt = f"{nxt} --force"
            return fail(args, 2, "refused", exc.reason, nxt=nxt)
        except OSError:
            return fail(args, 1, "error", "file")
        return CliSuccess({"name": name, "path": path, **meta}, redact=False)
    if as_raw:
        return CliRaw(value)
    return CliSuccess({"name": name, "value": value}, redact=False)


def run_secret(args, vault: Path) -> CliOutcome:
    bindings, err = _secret_env_bindings(args)
    if err is not None:
        return fail(args, 2, "refused", err, nxt=_RUN_HELP)
    for _var, name in bindings:
        invalid = resource_name_error(args, name, "secret", _RUN_HELP)
        if invalid is not None:
            return invalid
    child = _child_argv(args)
    if not child:
        return fail(args, 2, "refused", "need a command", nxt=_RUN_HELP)
    access = client(vault)
    protected_names = frozenset(access.protected_secret_names())
    unsafe = bool(getattr(args, "unsafe", False))
    for _var, name in bindings:
        if name in protected_names and not unsafe:
            return fail(
                args,
                2,
                "refused",
                f"{name} is protected",
                nxt="agentself secret run --env VAR=NAME --unsafe -- COMMAND",
            )
    child_env = host_env(os.environ.copy())
    assert child_env is not None
    values: list[str] = []
    env_names: list[str] = []
    secret_names: list[str] = []
    for var, name in bindings:
        value = access.get(name)
        child_env[var] = value
        values.append(value)
        env_names.append(var)
        secret_names.append(name)
    try:
        proc = subprocess.run(child, env=child_env, capture_output=True, check=False)
    except OSError:
        return fail(args, 1, "error", "command", nxt=_RUN_HELP)
    return CliSuccess(
        {
            "exit": int(proc.returncode),
            "names": secret_names,
            "env": env_names,
            "stdout": _redact_captured(proc.stdout, values),
            "stderr": _redact_captured(proc.stderr, values),
        }
    )


def update_secret(args, vault: Path) -> CliOutcome:
    invalid = resource_name_error(
        args, args.name, "secret", "agentself secret update --help"
    )
    if invalid is not None:
        return invalid
    value, err = secret_from_args(args)
    if err is not None:
        return secret_value_error(args, err)
    assert value is not None
    access = client(vault)
    protected_names = frozenset(access.protected_secret_names())
    if args.name in protected_names and not getattr(args, "unsafe", False):
        return fail(
            args,
            2,
            "refused",
            f"{args.name} is protected",
            nxt="agentself secret update NAME --unsafe",
        )
    access.update(args.name, value, unsafe=bool(getattr(args, "unsafe", False)))
    return CliSuccess({"name": args.name})


def list_secrets(args, vault: Path) -> CliOutcome:
    access = client(vault)
    names = access.list()
    protected_names = frozenset(access.protected_secret_names())
    protected = [name for name in names if name in protected_names]
    return CliSuccess({"names": names, "protected": protected})


def delete_secret(args, vault: Path) -> CliOutcome:
    invalid = resource_name_error(
        args, args.name, "secret", "agentself secret delete --help"
    )
    if invalid is not None:
        return invalid
    client(vault).delete(args.name)
    return CliSuccess({"name": args.name})


def secret_exists(args, vault: Path) -> CliOutcome:
    invalid = resource_name_error(
        args, args.name, "secret", "agentself secret exists --help"
    )
    if invalid is not None:
        return invalid
    found = client(vault).exists(args.name)
    if not found:
        return fail(
            args,
            3,
            "missing",
            nxt="agentself secret list",
            extra={"name": args.name, "exists": False},
        )
    return CliSuccess({"name": args.name, "exists": True})


def _child_argv(args) -> list[str]:
    argv = [str(part) for part in (getattr(args, "child", None) or [])]
    if argv[:1] == ["--"]:
        argv = argv[1:]
    return argv


def _parse_env_binding(raw: str) -> tuple[str, str] | None:
    text = (raw or "").strip()
    if "=" not in text:
        return None
    var, name = text.split("=", 1)
    var = var.strip()
    name = name.strip()
    if not var or not name or not _ENV_NAME.fullmatch(var):
        return None
    return var, name


def _secret_env_bindings(args) -> tuple[list[tuple[str, str]], str | None]:
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in getattr(args, "env_bindings", None) or []:
        pair = _parse_env_binding(raw)
        if pair is None:
            return [], "need VAR=NAME"
        var, _name = pair
        if var in seen:
            return [], "duplicate env"
        seen.add(var)
        items.append(pair)
    if not items:
        return [], "need VAR=NAME"
    return items, None


def _redact_captured(data: bytes, values: Sequence[str]) -> str:
    blob = data
    for value in sorted((item for item in values if item), key=len, reverse=True):
        blob = blob.replace(value.encode("utf-8"), b"[redacted]")
    return redact_secrets(blob.decode("utf-8", errors="replace"))


def _secret_bulk_requested(args) -> bool:
    return bool((getattr(args, "from_dir", None) or "").strip()) or bool(
        getattr(args, "from_files", None)
    )


def _parse_from_file_pair(raw: str) -> tuple[str, str] | None:
    text = (raw or "").strip()
    if "=" not in text:
        return None
    name, path = text.split("=", 1)
    name = name.strip()
    path = path.strip()
    if not name or not path:
        return None
    return name, path


def _secret_dir_files(folder: Path) -> list[Path]:
    entries: list[Path] = []
    for child in sorted(folder.iterdir(), key=lambda path: path.name):
        if child.name.startswith("."):
            continue
        if child.is_symlink() or not child.is_file():
            continue
        entries.append(child)
    return entries


def _secret_bulk_items(args) -> tuple[list[tuple[str, str]], str | None]:
    items: list[tuple[str, str]] = []
    named = (getattr(args, "name", None) or "").strip()
    from_file = (getattr(args, "from_file", None) or "").strip()
    if named or getattr(args, "value", None) is not None or from_file:
        return [], "name and --from-dir"
    for raw in getattr(args, "from_files", None) or []:
        pair = _parse_from_file_pair(raw)
        if pair is None:
            return [], "need a name"
        items.append(pair)
    folder = (getattr(args, "from_dir", None) or "").strip()
    if folder:
        root = Path(folder)
        try:
            if not root.is_dir():
                return [], "file"
            files = _secret_dir_files(root)
        except OSError:
            return [], "file"
        items.extend((path.name, str(path)) for path in files)
    if not items:
        return [], "need a value"
    return items, None


def _secret_create_one(access, name: str, value: str, *, unsafe: bool) -> str:
    if name == WALLET_KEY_NAME and not unsafe:
        return "refused"
    try:
        unchanged = access.create(name, value)
    except ProtectedName:
        return "refused"
    except Refused:
        return "refused"
    except ValueError:
        return "refused"
    return "unchanged" if unchanged else "created"


def _secret_create_bulk(vault: Path, args) -> CliOutcome:
    items, err = _secret_bulk_items(args)
    if err is not None:
        return secret_value_error(args, err)
    for name, _path in items:
        invalid = resource_name_error(
            args, name, "secret", "agentself secret create --help"
        )
        if invalid is not None:
            return invalid
    access = client(vault)
    created: list[str] = []
    unchanged: list[str] = []
    refused: list[str] = []
    unsafe = bool(getattr(args, "unsafe", False))
    for name, path in items:
        try:
            value = load_value_file(path, strip_newline=False)
        except (OSError, UnicodeDecodeError):
            refused.append(name)
            continue
        status = _secret_create_one(access, name, value, unsafe=unsafe)
        if status == "created":
            created.append(name)
        elif status == "unchanged":
            unchanged.append(name)
        else:
            refused.append(name)
    payload: dict[str, object] = {
        "created": created,
        "unchanged": unchanged,
        "refused": refused,
    }
    if created or unchanged:
        return CliSuccess(payload)
    return fail(
        args,
        2,
        "refused",
        "refused",
        nxt="agentself secret create --help",
        extra=payload,
    )

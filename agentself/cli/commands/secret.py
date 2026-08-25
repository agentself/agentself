from __future__ import annotations

from pathlib import Path

from agentself.cli.io import load_value_file, store_value_file, value_meta
from agentself.cli.outcomes import CliOutcome, CliRaw, CliSuccess
from agentself.cli.runtime import (
    client,
    fail,
    secret_from_args,
    secret_value_error,
)
from agentself.internal.custody.errors import ProtectedName, Refused
from agentself.internal.names import WALLET_KEY_NAME


def create_secret(args, vault: Path) -> CliOutcome:
    if _secret_bulk_requested(args):
        return _secret_create_bulk(client(vault), args)
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
    unchanged = access.create(args.name, value)
    payload: dict[str, object] = {"name": args.name}
    if unchanged:
        payload["unchanged"] = True
    return CliSuccess(payload)


def get_secret(args, vault: Path) -> CliOutcome:
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
            store_value_file(path, value)
        except OSError:
            return fail(args, 1, "error", "file")
        return CliSuccess({"name": name, "path": path, **meta}, redact=False)
    if as_raw:
        return CliRaw(value)
    return CliSuccess({"name": name, "value": value}, redact=False)


def update_secret(args, vault: Path) -> CliOutcome:
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
    client(vault).delete(args.name)
    return CliSuccess({"name": args.name})


def secret_exists(args, vault: Path) -> CliOutcome:
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


def _secret_create_bulk(access, args) -> CliOutcome:
    items, err = _secret_bulk_items(args)
    if err is not None:
        return secret_value_error(args, err)
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

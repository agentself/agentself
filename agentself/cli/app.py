from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import stat
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from agentself import __version__
from agentself.bind import public_recipient
from agentself.cli.io import (
    load_value_file,
    read_stdin_text,
    store_value_file,
    value_meta,
)
from agentself.cli.parser import _Parser, _parser
from agentself.host import (
    CHANNELS,
    ENV_LOG,
    UnknownBind,
    backends_payload,
    bind_of,
    format_backends,
    unknown_bind,
)
from agentself.internal.custody.errors import (
    CannotAuthorize,
    CannotSend,
    ChannelFailure,
    EmailSendNotReady,
    HostToolMissing,
    MissingSecret,
    NoGas,
    ProtectedName,
    Refused,
    StoreFailure,
    UnboundCaller,
    UnknownIdentity,
)
from agentself.internal.files import (
    LOCK_NAME,
    IdentityBusy,
    exclusive,
    have_host_tool,
)
from agentself.internal.format import format_version_error, load_json_file
from agentself.internal.log import NullLog, StreamLog
from agentself.internal.names import require_safe_token
from agentself.internal.setup import (
    SETUP_ACTION_REQUIRED,
    SETUP_CONNECTED,
    SETUP_FAILED,
    SETUP_INPUT_REQUIRED,
    SETUP_PENDING,
    continue_command,
    setup_status_of,
)
from agentself.internal.text import UTF8_BOM, sha256_text, strip_one_trailing_newline
from agentself.local import (
    DEFAULT_IDENTITY,
    IdentityStateError,
    bind_local,
    config_path,
    default_identity_dir,
    ensure_age_key,
    format_status,
    load_config,
    mail_domain,
    merge_config,
    redact_secrets,
    require_supported_formats,
    resolve_age_key_file,
    resolve_setting,
)

CLI_SCHEMA_VERSION = 1
_SEND_HUMAN = {
    "no_gas": "need gas",
    "insufficient_asset": "need funds",
}
_INSTALL_TOOLS_NEXT = "agentself install --tools"
_DIAGNOSE_NEXT = "agentself diagnose"
_SKILL_TARGETS = {
    "claude": Path(".claude") / "skills" / "agentself",
    "agents": Path(".agents") / "skills" / "agentself",
}


def _has_flag(argv: list[str], *names: str) -> bool:
    """True if a flag appears before ``--``. Positionals after ``--`` stay values."""

    wanted = frozenset(names)
    for token in argv:
        if token == "--":
            return False
        if token in wanted:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    as_json = _has_flag(raw, "--json")
    if _has_flag(raw, "--machine"):
        fake = argparse.Namespace(as_json=as_json)
        return _fail(
            fake,
            2,
            "unrecognized arguments: --machine\n",
            "refused",
            "unrecognized arguments: --machine",
            nxt="agentself --help",
        )
    if _has_flag(raw, "--version"):
        return _print_version(as_json)
    parser = _parser()
    _Parser._as_json = as_json
    try:
        args = parser.parse_args(raw)
    finally:
        _Parser._as_json = False
    args.as_json = bool(args.as_json or as_json)
    vault = default_identity_dir()

    if args.command == "install":
        return _install(args)
    if args.command == "backends":
        return _backends(args)
    if args.command in ("backup", "restore"):
        return _backup_restore(vault, args)

    from agentself.internal.host_tools import HostToolError, ensure_host_tools

    try:
        ensure_host_tools(fetch=False)
    except HostToolError as exc:
        return _fail(
            args,
            1,
            f"error: {exc}\n",
            "error",
            str(exc),
            nxt=_INSTALL_TOOLS_NEXT,
        )

    if args.command in ("init", "diagnose"):
        missing = _missing_host_tool(args.command == "init", vault, args)
        if missing is not None:
            return missing

    if args.command == "diagnose":
        return _diagnose(vault, args)
    if args.command == "init":
        return _init(vault, args)
    if args.command == "email" and getattr(args, "email_command", None) == "connect":
        return _email_connect(vault, args)

    client = None
    try:
        client = _client(vault)
    except UnknownBind as exc:
        return _bind_error(args, exc)
    except IdentityStateError as exc:
        return _identity_fail(args, exc)
    except Exception:
        return _fail(args, 1, "error\n", "error")

    try:
        if args.command in (None, "show"):
            view = client.identity()
            if _as_json(args):
                return _emit_ok(args, _status_json(view, vault))
            sys.stdout.write(format_status(view, vault))
            return 0
        if args.command == "secret":
            return _secret(client, args)
        if args.command == "email":
            return _email(client, args)
        if args.command == "wallet":
            return _wallet(client, args)
    except IdentityStateError as exc:
        return _identity_fail(args, exc)
    except UnboundCaller:
        return _not_initialized(args, bare=args.command in (None, "show"))
    except ValueError:
        return _fail(args, 2, "refused\n", "refused")
    except UnknownIdentity:
        return _fail(
            args, 2, "refused: unknown identity\n", "refused", "unknown identity"
        )
    except ProtectedName as exc:
        return _fail(
            args,
            2,
            f"refused: {exc}\n",
            "refused",
            str(exc),
            nxt="agentself secret list",
        )
    except Refused as exc:
        detail = str(exc).strip() or "refused"
        if detail == "refused":
            return _fail(args, 2, "refused\n", "refused")
        return _fail(args, 2, f"refused: {detail}\n", "refused", detail)
    except CannotAuthorize:
        return _fail(
            args,
            2,
            "refused: backend cannot authorize\n",
            "refused",
            "backend cannot authorize",
        )
    except CannotSend as exc:
        reason = exc.reason or "cannot_send"
        human = _SEND_HUMAN.get(reason, "backend cannot send")
        return _fail(args, 2, f"refused: {human}\n", "refused", reason)
    except NoGas as exc:
        reason = exc.reason or "no_gas"
        return _fail(args, 2, "refused: need gas\n", "refused", reason)
    except MissingSecret:
        return _fail(args, 3, "missing\n", "missing", nxt="agentself secret list")
    except EmailSendNotReady as exc:
        return _fail(
            args,
            1,
            f"error: {exc}\n",
            "error",
            exc.reason,
            nxt="agentself backends email",
        )
    except HostToolMissing as exc:
        return _fail_missing_tool(args, exc, vault)
    except ChannelFailure as exc:
        reason = exc.reason
        return _fail(
            args,
            1,
            f"error: {reason}\n",
            "error",
            reason,
            nxt=_channel_next(args),
        )
    except StoreFailure as exc:
        return _store_fail(args, exc)
    except FileNotFoundError as exc:
        detail = str(exc).strip()
        if detail.endswith("not on PATH"):
            nxt = _INSTALL_TOOLS_NEXT if "age" in detail or "sops" in detail else None
            return _fail(args, 1, f"error: {detail}\n", "error", detail, nxt=nxt)
        return _fail(args, 1, "error\n", "error")
    except Exception:
        return _fail(args, 1, "error\n", "error")
    return _fail(args, 1, "error\n", "error")


def _bundled_skill() -> Path:
    return Path(__file__).resolve().parent.parent / "skills" / "agentself" / "SKILL.md"


def _copy_skill_tree(src_dir: Path, dest_dir: Path) -> Path:
    """Copy packaged skill files without following links in either tree."""
    entries = sorted(src_dir.rglob("*"))
    if not entries:
        raise OSError("skill not packaged")
    dest_dir.mkdir(parents=True, exist_ok=True)
    if dest_dir.is_symlink():
        raise OSError("refusing linked skill destination")
    for src in entries:
        if src.is_symlink():
            raise OSError("refusing linked packaged skill path")
        relative = src.relative_to(src_dir)
        dest = dest_dir / relative
        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            if dest.is_symlink():
                raise OSError("refusing linked skill destination")
            continue
        if not src.is_file():
            raise OSError("refusing non-file packaged skill path")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink():
            raise OSError("refusing linked skill destination")
        shutil.copyfile(src, dest)
    return dest_dir / "SKILL.md"


def _install(args) -> int:
    skills = args.skills
    tools = args.tools
    if skills is None and not tools:
        return _fail(
            args,
            2,
            "need --skills or --tools\n",
            "refused",
            "need --skills or --tools",
            nxt="agentself install --skills",
        )
    payload: dict[str, object] = {"ok": True}
    paths: list[str] = []
    if tools:
        tool_err = _install_tools(args)
        if tool_err is not None:
            return tool_err
        from agentself.internal.host_tools import tools_dir

        payload["path"] = str(tools_dir())
    if skills is not None:
        paths, skill_err = _install_skills(args, skills)
        if skill_err is not None:
            return skill_err
        payload["paths"] = paths
    if _as_json(args):
        return _emit_ok(args, payload)
    if tools:
        sys.stdout.write(f"installed {payload['path']}\n")
    for path in paths:
        sys.stdout.write(f"installed {path}\n")
    return 0


def _install_tools(args) -> int | None:
    from agentself.internal.host_tools import (
        HostToolError,
        ensure_host_tools,
        fetch_enabled,
    )

    try:
        ensure_host_tools(fetch=True)
    except HostToolError as exc:
        return _fail(
            args,
            1,
            f"error: {exc}\n",
            "error",
            str(exc),
            nxt=_INSTALL_TOOLS_NEXT,
        )
    missing = [
        label
        for cmd, label in (("age-keygen", "age"), ("sops", "sops"))
        if not have_host_tool(cmd)
    ]
    if not missing:
        return None
    reason = (
        "host tool fetch is disabled"
        if not fetch_enabled()
        else f"{missing[0]} not on PATH"
    )
    return _fail(
        args,
        1,
        f"error: {reason}\n",
        "error",
        reason,
        nxt=_INSTALL_TOOLS_NEXT,
    )


def _install_skills(args, requested: str) -> tuple[list[str], int | None]:
    target = (requested or "").strip().lower()
    if target not in _SKILL_TARGETS:
        return [], _fail(
            args,
            2,
            "unknown skills target\n",
            "refused",
            "unknown skills target",
            nxt="agentself install --help",
        )
    src = _bundled_skill()
    if not src.is_file():
        return [], _fail(
            args,
            1,
            "error: skill not packaged\n",
            "error",
            "skill not packaged",
            nxt="agentself --help",
        )
    rel = _SKILL_TARGETS[target]
    dest_root = Path.home() if args.global_install else Path.cwd()
    dest_dir = dest_root / rel
    try:
        dest = _copy_skill_tree(src.parent, dest_dir)
    except OSError as exc:
        detail = str(exc).strip() or "could not install skill"
        return [], _fail(args, 1, f"error: {detail}\n", "error", detail)
    return [str(dest)], None


def _backends(args) -> int:
    channel = (args.channel or "").strip() or None
    if channel and channel not in CHANNELS:
        err = unknown_bind(channel, "") or "unknown channel"
        return _fail(
            args,
            2,
            f"{err}\n",
            "refused",
            err,
            nxt="agentself backends --help",
        )
    if _as_json(args):
        return _emit_ok(args, backends_payload(channel))
    sys.stdout.write(format_backends(channel))
    return 0


def _runtime_paths() -> dict[str, str]:
    package = str(Path(__file__).resolve().parent.parent)
    argv0 = str(sys.argv[0] or "")
    executable = argv0
    if argv0:
        try:
            executable = str(Path(argv0).resolve())
        except OSError:
            pass
    return {"package": package, "executable": executable}


def _print_version(as_json: bool) -> int:
    paths = _runtime_paths()
    if as_json:
        return _emit_ok(
            argparse.Namespace(as_json=True),
            {
                "version": __version__,
                "cli": CLI_SCHEMA_VERSION,
                **paths,
            },
        )
    sys.stdout.write(f"agentself {__version__}\n")
    return 0


def _registry_store_binding(vault: Path, identity_id: str) -> str | None:
    path = vault / "registry.json"
    if not path.is_file():
        return None
    try:
        data = load_json_file(path)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    err = format_version_error("registry.json", data)
    if err:
        raise IdentityStateError(err)
    if not identity_id:
        return None
    identities = data.get("identities")
    if not isinstance(identities, dict):
        return None
    raw = identities.get(identity_id)
    if not isinstance(raw, dict):
        return None
    binding = raw.get("store_binding")
    if isinstance(binding, str) and binding.strip():
        return binding.strip()
    return None


def _store_from_registry(vault: Path, cfg: dict[str, str]) -> str | None:
    if not config_path(vault).is_file():
        return None
    identity_id = (cfg.get("identity_id") or "").strip()
    recorded = _registry_store_binding(vault, identity_id)
    if recorded in CHANNELS["store"].names:
        return recorded
    return None


def _missing_host_tool(is_init: bool, vault: Path, args) -> int | None:
    store_name = CHANNELS["store"].default
    if is_init:
        store_name = getattr(args, "store", None) or store_name
    else:
        try:
            store_name = _store_from_registry(vault, load_config(vault)) or store_name
        except IdentityStateError:
            pass
    if not have_host_tool("age-keygen"):
        return _fail(
            args,
            1,
            "error: age not on PATH\n",
            "error",
            "age not on PATH",
            nxt=_INSTALL_TOOLS_NEXT,
        )
    bind = bind_of("store", store_name)
    tools = bind.tools if bind is not None else ()
    missing = [name for name in tools if not have_host_tool(name)]
    if not missing:
        return None
    reason = " and ".join(missing) + " not on PATH"
    return _fail(
        args,
        1,
        f"error: {reason}\n",
        "error",
        reason,
        nxt=_tools_next(missing, store_name),
    )


def _diagnose_tools(store_name: str) -> dict[str, bool]:
    bind = bind_of("store", store_name)
    names = ("age-keygen",) + (bind.tools if bind is not None else ())
    return {name: True for name in names}


def _tools_next(missing: list[str], store_name: str | None = None) -> str:
    from agentself.internal.host_tools import INSTALLABLE_TOOLS

    installable = set(INSTALLABLE_TOOLS)
    bind = bind_of("store", store_name) if store_name else None
    if bind is not None:
        installable.update(bind.installable_tools)
    if missing and all(name in installable for name in missing):
        return _INSTALL_TOOLS_NEXT
    return _DIAGNOSE_NEXT


def _fail_missing_tool(args, exc: HostToolMissing, vault: Path | None = None) -> int:
    missing = [
        part.strip()
        for part in str(exc.tool).replace(" and ", ",").split(",")
        if part.strip()
    ]
    store_name = getattr(args, "store", None) or None
    if not store_name and vault is not None:
        try:
            store_name = _store_from_registry(vault, load_config(vault))
        except IdentityStateError:
            pass
    if not store_name:
        store_name = CHANNELS["store"].default
    return _fail(
        args,
        1,
        f"error: {exc}\n",
        "error",
        str(exc),
        nxt=_tools_next(missing, store_name),
    )


def _diagnose(vault: Path, args) -> int:
    try:
        cfg = load_config(vault)
        initialized = config_path(vault).is_file()
        store_name = _store_from_registry(vault, cfg) or CHANNELS["store"].default
    except IdentityStateError as exc:
        return _identity_fail(args, exc)
    python = sys.version.split()[0]
    wallet_backend = email_backend = store_backend = None
    ready = {"wallet": False, "email": False, "store": False}
    problems: list[tuple[str, str]] = []
    if initialized:
        wallet_backend = (cfg.get("wallet_backend") or "").strip() or None
        email_backend = (cfg.get("email_backend") or "").strip() or None
        store_backend = store_name
        problems, ready = _diagnose_identity(vault, cfg, store_name)
    paths = _runtime_paths()
    tools = _diagnose_tools(store_name)
    payload: dict[str, object] = {
        "ok": not problems,
        "initialized": initialized,
        "ready": ready,
        "version": __version__,
        "python": python,
        "identity_dir": str(vault),
        "tools": tools,
        "wallet_backend": wallet_backend,
        "email_backend": email_backend,
        "store_backend": store_backend,
        **paths,
    }
    if problems:
        payload["problems"] = [item[0] for item in problems]
        payload["error"] = "error"
        payload["reason"] = problems[0][0]
        payload["next"] = problems[0][1]
    if _as_json(args):
        if problems:
            sys.stdout.write(redact_secrets(json.dumps(payload)) + "\n")
            return 1
        return _emit_ok(args, payload)
    lines = [
        f"version: {__version__}",
        f"python: {python}",
        f"package: {paths['package']}",
        f"executable: {paths['executable']}",
        f"identity_dir: {vault}",
    ]
    lines.extend(f"{name}: ok" for name in tools)
    lines.append(f"initialized: {'yes' if initialized else 'no'}")
    if initialized:
        if store_backend:
            lines.append(f"store_backend: {store_backend}")
        if wallet_backend:
            lines.append(f"wallet_backend: {wallet_backend}")
        if email_backend:
            lines.append(f"email_backend: {email_backend}")
        lines.append(f"ready.wallet: {str(ready['wallet']).lower()}")
        lines.append(f"ready.email: {str(ready['email']).lower()}")
        lines.append(f"ready.store: {str(ready['store']).lower()}")
    sys.stdout.write("\n".join(lines) + "\n")
    if problems:
        reason, nxt = problems[0]
        return _fail(args, 1, f"error: {reason}\n", "error", reason, nxt=nxt)
    return 0


def _diagnose_identity(
    vault: Path, cfg: dict[str, str], store_name: str
) -> tuple[list[tuple[str, str]], dict[str, bool]]:
    problems: list[tuple[str, str]] = []
    ready = {"wallet": False, "email": False, "store": False}
    checks = (
        ("wallet", (cfg.get("wallet_backend") or "").strip()),
        ("email", (cfg.get("email_backend") or "").strip()),
        ("store", store_name),
    )
    for channel, value in checks:
        if value and unknown_bind(channel, value):
            problems.append(
                (str(UnknownBind(channel, value)), f"agentself backends {channel}")
            )
    key_file = resolve_age_key_file(vault, cfg.get("age_key_file", ""))
    if not key_file or not Path(key_file).is_file():
        problems.append(("age key file is missing", "agentself init"))
    if problems:
        return problems, ready
    try:
        client = _client(vault)
        names = client.list()
    except UnboundCaller:
        problems.append(("age key file is not usable", "agentself init"))
        return problems, ready
    except UnknownBind as exc:
        problems.append((str(exc), f"agentself backends {exc.channel}"))
        return problems, ready
    except StoreFailure as exc:
        problems.append((_store_reason(exc), "agentself secret list"))
        return problems, ready
    except Exception:
        problems.append(("identity is not usable", "agentself init"))
        return problems, ready
    ready["store"] = True
    ready["email"] = "email.address" in names
    try:
        status = client.wallet_material_status()
    except StoreFailure as exc:
        problems.append((_store_reason(exc), "agentself secret list"))
        return problems, ready
    except Exception:
        problems.append(("identity is not usable", "agentself init"))
        return problems, ready
    if not status.get("ready"):
        missing = str(status.get("missing") or "wallet material")
        problems.append((f"{missing} is missing", "agentself init"))
        return problems, ready
    ready["wallet"] = True
    return problems, ready


def _require_bind(args, channel: str, value: str) -> int | None:
    if unknown_bind(channel, value) is None:
        return None
    return _bind_error(args, UnknownBind(channel, value))


def _bind_error(args, exc: UnknownBind) -> int:
    return _fail(
        args,
        2,
        f"{exc}\n",
        "refused",
        str(exc),
        nxt=f"agentself backends {exc.channel}",
    )


def _identity_fail(args, exc: IdentityStateError) -> int:
    return _fail(
        args,
        1,
        f"error: {exc}\n",
        "error",
        str(exc),
        nxt=_DIAGNOSE_NEXT,
    )


def _not_initialized(args, *, bare: bool = False) -> int:
    human = "not initialized\n" if bare else "refused: not initialized\n"
    return _fail(
        args,
        2,
        human,
        "refused",
        "not initialized",
        nxt="agentself init",
    )


def _cli_log():
    if os.environ.get(ENV_LOG, "").strip():
        return StreamLog()
    return NullLog()


def _compose():
    loaded = globals().get("compose")
    if loaded is not None:
        return loaded
    from agentself.compose import compose as loaded

    globals()["compose"] = loaded
    return loaded


def _client(vault: Path, **compose_kw):
    return _compose()(
        vault,
        log=_cli_log(),
        mail_domain=mail_domain(vault),
        bind=lambda: bind_local(vault),
        **compose_kw,
    )


def _init(vault: Path, args) -> int:
    try:
        require_supported_formats(vault)
        cfg = load_config(vault)
        store = args.store or CHANNELS["store"].default
        refused = _require_bind(args, "store", store)
        if refused is not None:
            return refused
        backends: dict[str, str] = {}
        for channel, spec in CHANNELS.items():
            if spec.env is None:
                continue
            value = resolve_setting(
                vault,
                spec.config_key or f"{channel}_backend",
                spec.env or "",
                spec.default,
                getattr(args, spec.name, None),
            )
            refused = _require_bind(args, channel, value)
            if refused is not None:
                return refused
            backends[channel] = value
        email_backend = backends["email"]
        wallet_backend = backends["wallet"]
        identity_id = _init_identity_id(vault, args)
        initialized = bool(cfg.get("identity_id") and cfg.get("age_key_file"))
        if initialized and not args.force:
            blocked = _init_mutation_refused(
                vault, args, cfg, identity_id, wallet_backend, email_backend, store
            )
            if blocked is not None:
                return blocked
        key = ensure_age_key(vault, identity_id)
        cfg = load_config(vault)
        age_key_file = _age_key_rel(vault, identity_id, cfg)
        identity_fields = {
            "identity_id": identity_id,
            "age_key_file": age_key_file,
        }
        backend_fields = {
            "email_backend": email_backend,
            "wallet_backend": wallet_backend,
        }
        if not cfg.get("identity_id") or not cfg.get("age_key_file"):
            merge_config(vault, identity_fields)
        recipient = public_recipient(str(key))
        client = _client(
            vault,
            email_backend=email_backend,
            wallet_backend=wallet_backend,
        )
        client.init(store)
        addr = client.wallet_address()
        merge_config(vault, {**identity_fields, **backend_fields})
        if _as_json(args):
            return _emit_ok(
                args,
                {
                    "id": identity_id,
                    "recipient": recipient,
                    "address": addr,
                    "wallet_backend": wallet_backend,
                    "email_backend": email_backend,
                },
            )
        text = (
            f"wallet: {addr}\n"
            f"wallet_backend: {wallet_backend}\n"
            f"recipient: {recipient}\n"
            f"email_backend: {email_backend}\n"
        )
        if email_backend == "agentmail":
            text += (
                "email_setup: agentself email connect; choose existing_credential, "
                "or create_account with explicit authorization\n"
            )
        sys.stdout.write(redact_secrets(text))
        return 0
    except UnboundCaller:
        return _not_initialized(args)
    except IdentityStateError as exc:
        return _identity_fail(args, exc)
    except UnknownBind as exc:
        return _bind_error(args, exc)
    except ValueError:
        return _fail(args, 2, "refused\n", "refused")
    except HostToolMissing as exc:
        return _fail_missing_tool(args, exc, vault)
    except StoreFailure as exc:
        return _store_fail(args, exc)
    except FileNotFoundError:
        return _fail(
            args,
            1,
            "error: age not on PATH\n",
            "error",
            "age not on PATH",
            nxt=_INSTALL_TOOLS_NEXT,
        )
    except RuntimeError as exc:
        detail = redact_secrets(str(exc).strip()) or "error"
        return _fail(args, 1, f"error: {detail}\n", "error", detail)
    except Exception:
        return _fail(args, 1, "error\n", "error")


def _age_key_rel(vault: Path, identity_id: str, cfg: dict[str, str]) -> str:
    stored = (cfg.get("age_key_file") or "").strip()
    resolved = resolve_age_key_file(vault, stored) if stored else ""
    if resolved and Path(resolved).is_file():
        return stored
    return f"identities/{identity_id}/agent.agekey"


def _init_identity_id(vault: Path, args) -> str:
    from agentself.host import ENV_IDENTITY_ID

    flagged = (args.identity_id or "").strip()
    if flagged:
        return require_safe_token(flagged, "identity id")
    existing = load_config(vault).get("identity_id", "").strip()
    if existing:
        return require_safe_token(existing, "identity id")
    env_id = os.environ.get(ENV_IDENTITY_ID, "").strip()
    if env_id:
        return require_safe_token(env_id, "identity id")
    asked = "" if _as_json(args) else _ask_identity_name()
    if asked:
        return require_safe_token(asked, "identity id")
    return DEFAULT_IDENTITY


def _init_mutation_refused(
    vault: Path,
    args,
    cfg: dict[str, str],
    identity_id: str,
    wallet_backend: str,
    email_backend: str,
    store: str,
) -> int | None:
    current_id = (cfg.get("identity_id") or "").strip()
    current_wallet = (cfg.get("wallet_backend") or "").strip()
    current_email = (cfg.get("email_backend") or "").strip()
    recorded = _registry_store_binding(vault, current_id) if current_id else None
    changing = (
        (current_id and current_id != identity_id)
        or (current_wallet and current_wallet != wallet_backend)
        or (current_email and current_email != email_backend)
        or (recorded and recorded != store)
    )
    if not changing:
        return None
    return _fail(
        args,
        2,
        "refused: identity already initialized\n",
        "refused",
        "identity already initialized",
        nxt="agentself init --force",
    )


def _ask_identity_name() -> str:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return ""
    try:
        return input("Name this identity [agent]: ").strip()
    except EOFError:
        return ""


def _email_connect(vault: Path, args) -> int:
    try:
        answers, err = _connect_answers(args)
        if err is not None:
            return err
        result = _client(vault).email_connect(
            answers=answers or None,
            state=(args.setup_state or "").strip() or None,
        )
        return _email_connect_result(vault, args, result)
    except UnknownBind as exc:
        return _bind_error(args, exc)
    except UnboundCaller:
        return _not_initialized(args)
    except ChannelFailure as exc:
        return _email_connect_channel_fail(args, exc)
    except HostToolMissing as exc:
        return _fail_missing_tool(args, exc, vault)
    except Refused:
        return _fail(args, 2, "refused\n", "refused")
    except StoreFailure as exc:
        return _store_fail(args, exc)
    except Exception:
        return _fail(args, 1, "error\n", "error")


def _connect_answers(args) -> tuple[dict[str, str], int | None]:
    do_continue = args.do_continue
    state = (args.setup_state or "").strip()
    path = (args.result_file or "").strip()
    if not do_continue:
        if path:
            return {}, _fail(
                args,
                2,
                "refused: --result-file needs --continue\n",
                "refused",
                "--result-file needs --continue",
                nxt="agentself email connect --help",
            )
        if state:
            return {}, _fail(
                args,
                2,
                "refused: --state needs --continue\n",
                "refused",
                "--state needs --continue",
                nxt="agentself email connect --help",
            )
        return {}, None
    if not state:
        return {}, _fail(
            args,
            2,
            "refused: --continue needs --state\n",
            "refused",
            "--continue needs --state",
            nxt="agentself email connect --help",
        )
    if path:
        try:
            text = load_value_file(path)
        except OSError:
            return {}, _fail(
                args,
                1,
                "error: file\n",
                "error",
                "file",
                nxt="agentself email connect --help",
            )
        return {"value": text} if text else {}, None
    if sys.stdin.isatty():
        return {}, None
    text = read_stdin_text()
    return {"value": text} if text else {}, None


def _email_connect_result(vault: Path, args, result: dict[str, object]) -> int:
    status = setup_status_of(result)
    if status == SETUP_CONNECTED:
        addr = str(result.get("address") or "").strip()
        if not addr:
            return _fail(
                args,
                1,
                "error: no inbox\n",
                "error",
                "no inbox",
                nxt="agentself backends email",
            )
        return _email_connect_ok(args, addr)
    if status == SETUP_FAILED:
        reason = str(result.get("reason") or "error")
        return _fail(
            args,
            1,
            f"error: {reason}\n",
            "error",
            reason,
            nxt="agentself backends email",
            extra=_setup_public(result),
        )
    if not (
        status in (SETUP_INPUT_REQUIRED, SETUP_ACTION_REQUIRED, SETUP_PENDING)
        and not _as_json(args)
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    ):
        return _email_setup_pending(args, result, status)
    prompted = _prompt_setup_option(result)
    if prompted is None:
        return _email_setup_pending(args, result, status)
    if not prompted:
        return _fail(
            args,
            3,
            "nothing entered\n",
            "missing",
            "nothing entered",
            nxt="agentself email connect",
        )
    option = result.get("option")
    if isinstance(option, dict) and (
        str(option.get("type") or "").strip().lower() == "secret"
        or bool(option.get("sensitive"))
    ):
        sys.stdout.write("Checking the credential...\n")
        setattr(args, "_interactive_email_credential_stored", True)
    nxt = _client(vault).email_connect(
        answers=prompted,
        state=str(result.get("state") or "") or None,
    )
    return _email_connect_result(vault, args, nxt)


def _print_setup_action(action: dict[str, object]) -> None:
    """Display a backend-provided external action without interpreting it."""

    label = str(action.get("label") or "Open link").strip()
    url = str(action.get("url") or "").strip()
    if not url:
        return
    sys.stdout.write(f"{label}:\n{url}\n\n")


def _prompt_setup_option(result: dict[str, object]) -> dict[str, str] | None:
    option = result.get("option")
    if not isinstance(option, dict):
        return None
    name = str(option.get("name") or "").strip()
    if not name:
        return None
    words = name.replace("_", " ")
    article = "an" if words[:1].lower() in "aeiou" else "a"
    sys.stdout.write(f"Email setup needs {article} {words}.\n\n")
    action = option.get("action")
    if isinstance(action, dict):
        _print_setup_action(action)
    prompt = str(option.get("prompt") or name).strip()
    option_type = str(option.get("type") or "string").strip().lower()
    sensitive = option_type == "secret" or bool(option.get("sensitive"))
    choices = [
        str(choice).strip()
        for choice in (option.get("choices") or [])
        if str(choice).strip()
    ]
    try:
        if option_type == "choice" and choices:
            sys.stdout.write(prompt + ":\n")
            for index, choice in enumerate(choices, 1):
                sys.stdout.write(f"{index}. {choice}\n")
            raw = input(f"Choose [1-{len(choices)}]: ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(choices):
                return {name: choices[int(raw) - 1]}
            return {}
        suffix = " (input is hidden)" if sensitive else ""
        sys.stdout.write(f"{prompt}{suffix}: ")
        sys.stdout.flush()
        if sensitive:
            try:
                value = getpass.getpass("", stream=sys.stdout)
            except TypeError:
                value = getpass.getpass("")
        else:
            value = input("")
    except EOFError:
        return {}
    if not value:
        return {}
    if value.startswith(UTF8_BOM):
        value = value[len(UTF8_BOM) :]
    value = strip_one_trailing_newline(value)
    if not value:
        return {}
    return {name: value}


def _setup_public(result: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {
        key: result[key]
        for key in (
            "status",
            "state",
            "option",
            "human_action_required",
            "continue",
            "message",
        )
        if key in result
    }
    if "continue" not in payload and result.get("state"):
        payload["continue"] = continue_command(str(result["state"]))
    if "human_action_required" not in payload:
        payload["human_action_required"] = (
            setup_status_of(result) == SETUP_ACTION_REQUIRED
        )
    return payload


def _email_setup_pending(args, result: dict[str, object], status: str) -> int:
    token = str(result.get("state") or "")
    nxt = str(result.get("continue") or "") or (
        continue_command(token) if token else "agentself email connect --help"
    )
    extra = _setup_public(result)
    reason = {
        SETUP_ACTION_REQUIRED: "human action required",
        SETUP_PENDING: "pending",
    }.get(status, "input required")
    human = f"{reason}\n"
    option = extra.get("option")
    if isinstance(option, dict) and option.get("name"):
        human = f"{reason}: {option['name']}\n"
    return _fail(
        args,
        3,
        human,
        "missing",
        reason,
        nxt=nxt,
        extra=extra,
    )


def _email_connect_channel_fail(args, exc: ChannelFailure) -> int:
    reason = exc.reason
    if reason == "no_token":
        return _fail(
            args,
            3,
            "need email.credential\n",
            "missing",
            "need email.credential",
            nxt="agentself --json email connect",
        )
    if reason == "need_address":
        return _fail(
            args,
            3,
            "need email.address\n",
            "missing",
            "need email.address",
            nxt="agentself --json email connect",
        )
    if reason == "invalid_credential":
        nxt = (
            "agentself --json email connect"
            if _as_json(args)
            else "agentself email connect"
        )
        return _fail(
            args,
            1,
            "error: invalid credentials\n",
            "error",
            "invalid credentials",
            nxt=nxt,
        )
    return _fail(
        args,
        1,
        f"error: {reason}\n",
        "error",
        reason,
        nxt="agentself backends email",
    )


def _secret(client, args) -> int:
    verb = args.secret_command
    if verb == "create":
        value, err = _secret_from_args(args)
        if err is not None:
            return _secret_value_error(args, err)
        unchanged = client.create(args.name, value)
        payload: dict[str, object] = {"name": args.name}
        if unchanged:
            payload["unchanged"] = True
        if _as_json(args):
            return _emit_ok(args, payload)
        return 0
    if verb == "get":
        return _secret_get(client, args)
    if verb == "exists":
        found = client.exists(args.name)
        if not found:
            return _fail(
                args,
                3,
                "missing\n",
                "missing",
                nxt="agentself secret list",
                extra={"name": args.name, "exists": False},
            )
        if _as_json(args):
            return _emit_ok(args, {"name": args.name, "exists": True})
        sys.stdout.write("yes\n")
        return 0
    if verb == "update":
        value, err = _secret_from_args(args)
        if err is not None:
            return _secret_value_error(args, err)
        protected_names = frozenset(client.protected_secret_names())
        if args.name in protected_names and not getattr(args, "unsafe", False):
            return _fail(
                args,
                2,
                f"refused: {args.name} is protected\n",
                "refused",
                f"{args.name} is protected",
                nxt="agentself secret update NAME --unsafe",
            )
        client.update(args.name, value, unsafe=bool(getattr(args, "unsafe", False)))
        if _as_json(args):
            return _emit_ok(args, {"name": args.name})
        return 0
    if verb == "list":
        names = client.list()
        protected_names = frozenset(client.protected_secret_names())
        protected = [name for name in names if name in protected_names]
        if _as_json(args):
            return _emit_ok(args, {"names": names, "protected": protected})
        for name in names:
            suffix = " (protected)" if name in protected_names else ""
            sys.stdout.write(name + suffix + "\n")
        return 0
    if verb == "delete":
        client.delete(args.name)
        if _as_json(args):
            return _emit_ok(args, {"name": args.name})
        sys.stdout.write("ok\n")
        return 0
    return 1


def _secret_get(client, args) -> int:
    name = args.name
    path = (args.to_file or "").strip()
    protected_names = frozenset(client.protected_secret_names())
    if name in protected_names and not args.unsafe and not args.meta:
        return _fail(
            args,
            2,
            f"refused: {name} is protected\n",
            "refused",
            f"{name} is protected",
            nxt="agentself secret get NAME --unsafe --file PATH",
        )
    if not args.meta and not path and not args.print_value:
        return _fail(
            args,
            2,
            "refused: choose --file, --meta, or --print\n",
            "refused",
            "choose --file, --meta, or --print",
            nxt="agentself secret get NAME --file PATH",
        )
    value = client.get(name)
    meta = value_meta(value)
    if args.meta:
        payload = {"name": name, **meta, "protected": name in protected_names}
        if _as_json(args):
            return _emit_ok(args, payload)
        sys.stdout.write(f"name: {name}\n")
        sys.stdout.write(f"bytes: {meta['bytes']}\n")
        sys.stdout.write(f"sha256: {meta['sha256']}\n")
        return 0
    if path:
        try:
            store_value_file(path, value)
        except OSError:
            return _fail(args, 1, "error: file\n", "error", "file")
        if _as_json(args):
            return _emit_ok(args, {"name": name, "path": path, **meta}, redact=False)
        return 0
    if _as_json(args):
        return _emit_ok(args, {"name": name, "value": value}, redact=False)
    if value.endswith("\r\n"):
        value = value[:-2] + "\n"
    elif value.endswith("\r"):
        value = value[:-1] + "\n"
    elif not value.endswith("\n"):
        value += "\n"
    sys.stdout.write(value)
    return 0


def _email(client, args) -> int:
    if args.email_command == "show":
        email = client.identity().get("email")
        email = email if isinstance(email, dict) else {}
        ready = bool(email.get("owned_address") and email.get("address"))
        if _as_json(args):
            return _emit_ok(args, {**email, "ready": ready})
        if ready:
            sys.stdout.write(str(email["address"]) + "\n")
        else:
            sys.stdout.write("not configured\n")
        return 0
    if args.email_command == "send":
        client.email_send(args.to, args.subject, args.body)
        if _as_json(args):
            return _emit_ok(args, {"to": args.to, "subject": args.subject})
        return 0
    if args.email_command == "mark":
        acted = args.mark_state == "acted"
        client.email_mark(args.message_id, acted=acted)
        payload = {"id": args.message_id, "acted": acted}
        if _as_json(args):
            return _emit_ok(args, payload)
        sys.stdout.write(args.mark_state + "\n")
        return 0
    if args.email_command in ("receive", "list", "find"):
        if (
            args.email_command == "receive"
            and (args.body_file or "").strip()
            and not (args.message_id or "").strip()
        ):
            return _fail(
                args,
                2,
                "refused: --file requires a message ref or ID\n",
                "refused",
                "--file requires a message ref or ID",
                nxt="agentself email receive REF --file PATH",
            )
        messages = (
            client.email_receive(
                message_id=args.message_id,
                include_body=bool(args.body_file or args.print_body),
            )
            if args.email_command == "receive"
            else (
                client.email_find(
                    args.query, status=args.status, acted=args.acted_filter
                )
                if args.email_command == "find"
                else client.email_list(status=args.status, acted=args.acted_filter)
            )
        )
        if args.email_command == "receive":
            file_error = _prepare_received_messages(messages, args)
            if file_error is not None:
                return file_error
        if _as_json(args):
            return _emit_ok(args, {"messages": messages})
        for msg in messages:
            sys.stdout.write(json.dumps(msg) + "\n")
        return 0
    return 1


def _prepare_received_messages(messages: list[dict[str, object]], args) -> int | None:
    path = (args.body_file or "").strip()
    if path and messages:
        body = str(messages[0].get("body", ""))
        try:
            store_value_file(path, body)
        except OSError:
            return _fail(args, 1, "error: file\n", "error", "file")
        messages[0]["body_file"] = path
        messages[0]["body_bytes"] = str(value_meta(body)["bytes"])
        messages[0]["body_sha256"] = str(value_meta(body)["sha256"])
    if not args.print_body:
        for message in messages:
            message.pop("body", None)
    return None


def _wallet(client, args) -> int:
    if args.wallet_command in ("show", "address"):
        addr = client.wallet_address()
        if _as_json(args):
            return _emit_ok(args, {"address": addr})
        sys.stdout.write(addr + "\n")
        return 0
    if args.wallet_command == "balance":
        bal = client.wallet_balance()
        if _as_json(args):
            return _emit_ok(args, dict(bal))
        sys.stdout.write(json.dumps(bal) + "\n")
        return 0
    if args.wallet_command == "authorize":
        message, err = _message_from_args(args)
        if err is not None or message is None:
            return _fail(
                args,
                3,
                f"missing: {err or 'need a value'}\n",
                "missing",
                err or "need a value",
                nxt="agentself wallet authorize --help",
            )
        token = client.wallet_authorize(message)
        addr = client.wallet_address()
        view = client.identity().get("wallet")
        wallet = view if isinstance(view, dict) else {}
        payload = {
            "address": addr,
            "scheme": str(wallet.get("scheme") or ""),
            "network": str(wallet.get("chain") or ""),
            "message_sha256": sha256_text(message),
            "authorization": token,
        }
        if _as_json(args):
            return _emit_ok(args, payload)
        sys.stdout.write(token + "\n")
        return 0
    if args.wallet_command == "verify":
        path = (args.from_file or "").strip()
        authorization = (args.authorization or "").strip()
        leftover = (args.message or "").strip()
        if path and leftover and not authorization:
            authorization = leftover
            args.message = ""
        message, err = _message_from_args(args)
        if err is not None or message is None:
            return _fail(
                args,
                3,
                f"missing: {err or 'need a value'}\n",
                "missing",
                err or "need a value",
                nxt="agentself wallet verify --help",
            )
        if not authorization:
            return _fail(
                args,
                3,
                "missing: need an authorization\n",
                "missing",
                "need an authorization",
                nxt="agentself wallet verify --help",
            )
        checked = client.wallet_verify(message, authorization)
        scheme = str(checked.get("scheme") or "").strip()
        if not scheme:
            return _fail(
                args,
                1,
                "error: missing scheme\n",
                "error",
                "missing scheme",
                nxt="agentself backends wallet",
            )
        valid = bool(checked.get("valid"))
        payload = {
            "valid": valid,
            "address": checked.get("address"),
            "scheme": scheme,
        }
        if _as_json(args):
            if valid:
                return _emit_ok(args, payload)
            return _fail(
                args,
                2,
                "invalid\n",
                "refused",
                "invalid authorization",
                nxt="agentself wallet verify --help",
                extra=payload,
            )
        sys.stdout.write("valid\n" if valid else "invalid\n")
        return 0 if valid else 2
    if args.wallet_command == "send":
        sent = client.wallet_send(args.to, args.amount, args.asset or "")
        payload = {
            "to": args.to,
            "amount": _canonical_amount(args.amount),
            "asset": sent["asset"],
        }
        if sent.get("hash"):
            payload["hash"] = sent["hash"]
        if _as_json(args):
            return _emit_ok(args, payload, redact=False)
        if payload.get("hash"):
            sys.stdout.write(payload["hash"] + "\n")
        return 0
    return 1


def _canonical_amount(value: str) -> str:
    try:
        text = format(Decimal(str(value).strip()), "f")
    except (InvalidOperation, ValueError):
        return str(value)
    return text.rstrip("0").rstrip(".") if "." in text else text


def _backup_restore(vault: Path, args) -> int:
    src = vault if args.command == "backup" else Path(args.path)
    dest = Path(args.path) if args.command == "backup" else vault
    try:
        try:
            with exclusive(vault):
                _copy_identity_dir(src, dest, force=args.force)
        except IdentityBusy as exc:
            raise IdentityStateError("identity directory busy") from exc
    except IdentityStateError as exc:
        return _fail(
            args,
            1,
            f"error: {exc}\n",
            "error",
            str(exc),
            nxt=f"agentself {args.command} --help",
        )
    except OSError as exc:
        detail = str(exc).strip() or "copy failed"
        return _fail(args, 1, f"error: {detail}\n", "error", detail)
    shown = str(dest)
    if _as_json(args):
        return _emit_ok(args, {"path": shown})
    sys.stdout.write(f"ok\npath: {shown}\n")
    return 0


def _ignore_identity_junk(directory: str, names: list[str]) -> set[str]:
    del directory
    return {name for name in names if name.endswith(".tmp") or name == LOCK_NAME}


def _copy_identity_file(src: str, dest: str, *, follow_symlinks: bool = True) -> str:
    del follow_symlinks
    try:
        mode = os.lstat(src).st_mode
    except OSError:
        return dest
    if stat.S_ISLNK(mode):
        if os.path.lexists(dest):
            os.unlink(dest)
        os.symlink(os.readlink(src), dest)
        return dest
    if not stat.S_ISREG(mode):
        return dest
    shutil.copy2(src, dest, follow_symlinks=False)
    return dest


def _install_staged(staging: Path, dest: Path) -> None:
    if not dest.exists():
        os.rename(staging, dest)
        return
    prev = dest.with_name(dest.name + ".agentself-prev")
    if prev.exists():
        shutil.rmtree(prev)
    try:
        os.rename(dest, prev)
    except OSError:
        _replace_tree_contents(staging, dest)
        return
    try:
        os.rename(staging, dest)
    except OSError:
        os.rename(prev, dest)
        raise
    shutil.rmtree(prev, ignore_errors=True)


def _replace_tree_contents(staging: Path, dest: Path) -> None:
    keep = {LOCK_NAME}
    prev = dest.with_name(dest.name + ".agentself-prev")
    if prev.exists():
        shutil.rmtree(prev)
    prev.mkdir(mode=0o700)
    done = False
    try:
        for child in list(dest.iterdir()):
            if child.name in keep:
                continue
            os.rename(child, prev / child.name)
        for child in list(staging.iterdir()):
            if child.name in keep:
                continue
            os.rename(child, dest / child.name)
        done = True
    except Exception:
        leftover = [path for path in dest.iterdir() if path.name not in keep]
        if not leftover:
            for child in list(prev.iterdir()):
                os.rename(child, dest / child.name)
        raise
    finally:
        if done:
            shutil.rmtree(prev, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)


def _copy_identity_dir(src: Path, dest: Path, *, force: bool) -> None:
    if not src.is_dir():
        raise IdentityStateError("identity directory is missing")
    try:
        src_r = src.resolve()
        dest_r = dest.resolve()
    except OSError as exc:
        raise IdentityStateError("cannot read path") from exc
    if dest_r == src_r:
        raise IdentityStateError("destination is the identity directory")
    if dest_r.is_relative_to(src_r):
        raise IdentityStateError("destination is inside the identity directory")
    if src_r.is_relative_to(dest_r):
        raise IdentityStateError("destination contains the identity directory")
    if not (src / "config.json").is_file():
        raise IdentityStateError("identity directory is missing")
    if dest.exists():
        if dest.is_file():
            raise IdentityStateError("destination exists")
        try:
            meaningful = [path for path in dest.iterdir() if path.name != LOCK_NAME]
        except OSError as exc:
            raise IdentityStateError("cannot read destination") from exc
        if meaningful and not force:
            raise IdentityStateError("destination is not empty")
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.with_name(dest.name + ".agentself-staging")
    try:
        staging_r = staging.resolve()
    except OSError as exc:
        raise IdentityStateError("cannot read path") from exc
    if staging_r == src_r or staging_r.is_relative_to(src_r):
        raise IdentityStateError("destination is inside the identity directory")
    if staging.exists():
        shutil.rmtree(staging)
    try:
        shutil.copytree(
            src,
            staging,
            copy_function=_copy_identity_file,
            ignore=_ignore_identity_junk,
            symlinks=True,
        )
        _install_staged(staging, dest)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def _as_json(args) -> bool:
    return bool(getattr(args, "as_json", False))


def _emit_ok(args, payload: dict[str, object], *, redact: bool = True) -> int:
    body = {"ok": True, **{key: value for key, value in payload.items() if key != "ok"}}
    text = json.dumps(body)
    if redact:
        text = redact_secrets(text)
    sys.stdout.write(text + "\n")
    return 0


def _email_connect_ok(args, address: str | None) -> int:
    addr = (address or "").strip() or None
    if _as_json(args):
        return _emit_ok(args, {"address": addr, "status": SETUP_CONNECTED})
    if addr:
        if getattr(args, "_interactive_email_credential_stored", False):
            sys.stdout.write(
                f"Connected: {addr}\nThe credential is encrypted in this identity.\n"
            )
        else:
            sys.stdout.write(f"email: {addr}\n")
    else:
        sys.stdout.write("email: not configured\n")
    return 0


def _channel_next(args) -> str:
    command = getattr(args, "command", None)
    if command == "email":
        return "agentself backends email"
    if command == "wallet":
        return "agentself backends wallet"
    if command == "secret":
        return "agentself secret --help"
    return "agentself --help"


def _store_reason(exc: StoreFailure) -> str:
    if exc.name:
        return f"cannot read {exc.name}"
    return str(exc).strip() or "store error"


def _store_fail(args, exc: StoreFailure) -> int:
    reason = _store_reason(exc)
    return _fail(
        args,
        1,
        f"error: {reason}\n",
        "error",
        reason,
        nxt=_DIAGNOSE_NEXT,
    )


def _secret_value_error(args, err: str) -> int:
    nxt = (
        "agentself secret update --help"
        if getattr(args, "secret_command", None) == "update"
        else "agentself secret create --help"
    )
    if err == "need a value":
        return _fail(args, 3, f"missing: {err}\n", "missing", err, nxt=nxt)
    return _fail(args, 1, f"error: {err}\n", "error", err, nxt=nxt)


def _default_json_next(args, error: str) -> str:
    if error == "missing":
        verb = getattr(args, "secret_command", None)
        if verb == "create":
            return "agentself secret create --help"
        if verb == "update":
            return "agentself secret update --help"
        if verb in ("get", "delete", "list", "exists"):
            return "agentself secret list"
        return "agentself --help"
    if error == "refused":
        return _channel_next(args)
    return _DIAGNOSE_NEXT


def _fail(
    args,
    exit_code: int,
    human: str,
    error: str,
    reason: str | None = None,
    nxt: str | None = None,
    extra: dict[str, object] | None = None,
) -> int:
    human = redact_secrets(human)
    if reason is not None:
        reason = redact_secrets(reason)
    else:
        reason = error
    if nxt and "next:" not in human:
        if not human.endswith("\n"):
            human += "\n"
        human += f"next: {nxt}\n"
    if _as_json(args):
        payload: dict[str, object] = {
            "ok": False,
            "error": error,
            "reason": reason,
            "next": nxt if nxt is not None else _default_json_next(args, error),
        }
        if extra:
            for key, value in extra.items():
                if key not in payload:
                    payload[key] = value
        sys.stdout.write(json.dumps(payload) + "\n")
    else:
        sys.stderr.write(human)
    return exit_code


def _status_json(view: dict[str, object], vault: Path) -> dict[str, object]:
    raw_wallet = view.get("wallet")
    wallet: dict[str, object] = raw_wallet if isinstance(raw_wallet, dict) else {}
    raw_email = view.get("email")
    email: dict[str, object] = raw_email if isinstance(raw_email, dict) else {}
    addr = wallet.get("address")
    email_ready = bool(email.get("owned_address") and email.get("address"))
    return {
        "id": view.get("id"),
        "recipient": view.get("recipient"),
        "address": addr,
        "wallet_backend": view.get("wallet_backend"),
        "email_backend": view.get("email_backend"),
        "identity_dir": str(vault),
        "email": {**email, "ready": email_ready},
        "ready": {"email": email_ready},
    }


def _secret_from_args(args) -> tuple[str | None, str | None]:
    argv_value = args.value
    path = (args.from_file or "").strip()
    if argv_value is not None and path:
        return None, "value and --file"
    if path:
        try:
            return load_value_file(path, strip_newline=False), None
        except (OSError, UnicodeDecodeError):
            return None, "file"
    if argv_value is not None:
        return argv_value, None
    if sys.stdin.isatty():
        return None, "need a value"
    try:
        return read_stdin_text(), None
    except UnicodeDecodeError:
        return None, "file"


def _message_from_args(args) -> tuple[str | None, str | None]:
    argv_value = args.message
    path = (args.from_file or "").strip()
    if argv_value is not None and str(argv_value) != "" and path:
        return None, "message and --file"
    if path:
        try:
            return load_value_file(path), None
        except (OSError, UnicodeDecodeError):
            return None, "file"
    if argv_value is not None and str(argv_value) != "":
        return str(argv_value), None
    if sys.stdin.isatty():
        return None, "need a value"
    try:
        text = read_stdin_text()
    except UnicodeDecodeError:
        return None, "file"
    if not text:
        return None, "need a value"
    return text, None


def run() -> None:
    raise SystemExit(main())

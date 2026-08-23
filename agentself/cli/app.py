from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import sys
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
from agentself.internal.format import format_version_error
from agentself.internal.log import NullLog, StreamLog
from agentself.internal.names import (
    PROTECTED_SECRET_NAMES,
    require_safe_token,
)
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

CLI_SCHEMA_VERSION = 2
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
    args.as_json = bool(getattr(args, "as_json", False) or as_json)
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
            f"error: {exc}\nnext: {_INSTALL_TOOLS_NEXT}\n",
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
        return _fail(
            args,
            1,
            f"error: {exc}\n",
            "error",
            str(exc),
            nxt=_DIAGNOSE_NEXT,
        )
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
        return _fail(
            args,
            1,
            f"error: {exc}\n",
            "error",
            str(exc),
            nxt=_DIAGNOSE_NEXT,
        )
    except UnboundCaller:
        if args.command in (None, "show"):
            return _fail(
                args,
                2,
                "not initialized\n",
                "refused",
                "not initialized",
                nxt="agentself init",
            )
        return _fail(
            args,
            2,
            "refused: not initialized\n",
            "refused",
            "not initialized",
            nxt="agentself init",
        )
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
    except Refused:
        return _fail(args, 2, "refused\n", "refused")
    except CannotAuthorize:
        return _fail(
            args,
            2,
            "refused: backend cannot authorize\n",
            "refused",
            "backend cannot authorize",
        )
    except CannotSend as exc:
        reason = getattr(exc, "reason", None) or "cannot_send"
        human = _SEND_HUMAN.get(reason, "backend cannot send")
        return _fail(args, 2, f"refused: {human}\n", "refused", reason)
    except NoGas as exc:
        reason = getattr(exc, "reason", None) or "no_gas"
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
        return _fail_missing_tool(args, exc)
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


def _install(args) -> int:
    skills = getattr(args, "skills", None)
    tools = bool(getattr(args, "tools", False))
    if skills is None and not tools:
        return _fail(
            args,
            2,
            "need --skills or --tools\nnext: agentself install --skills\n",
            "refused",
            "need --skills or --tools",
            nxt="agentself install --skills",
        )
    payload: dict[str, object] = {"ok": True}
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
    installed = payload.get("paths")
    if isinstance(installed, list):
        for path in installed:
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
            f"error: {exc}\nnext: {_INSTALL_TOOLS_NEXT}\n",
            "error",
            str(exc),
            nxt=_INSTALL_TOOLS_NEXT,
        )
    missing = []
    if shutil.which("age-keygen") is None:
        missing.append("age")
    if shutil.which("sops") is None:
        missing.append("sops")
    if not missing:
        return None
    if not fetch_enabled():
        reason = "host tool fetch is disabled"
        return _fail(
            args,
            1,
            f"error: {reason}\nnext: {_INSTALL_TOOLS_NEXT}\n",
            "error",
            reason,
            nxt=_INSTALL_TOOLS_NEXT,
        )
    reason = f"{missing[0]} not on PATH"
    return _fail(
        args,
        1,
        f"error: {reason}\nnext: {_INSTALL_TOOLS_NEXT}\n",
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
            "unknown skills target\nnext: agentself install --help\n",
            "refused",
            "unknown skills target",
            nxt="agentself install --help",
        )
    src = _bundled_skill()
    if not src.is_file():
        return [], _fail(
            args,
            1,
            "error: skill not packaged\nnext: agentself --help\n",
            "error",
            "skill not packaged",
            nxt="agentself --help",
        )
    rel = _SKILL_TARGETS[target]
    dest_root = Path.home() if getattr(args, "global_install", False) else Path.cwd()
    dest_dir = dest_root / rel
    try:
        body = src.read_text(encoding="utf-8")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "SKILL.md"
        dest.write_text(body, encoding="utf-8")
    except OSError as exc:
        detail = str(exc).strip() or "could not install skill"
        return [], _fail(args, 1, f"error: {detail}\n", "error", detail)
    return [str(dest)], None


def _backends(args) -> int:
    channel = (getattr(args, "channel", None) or "").strip() or None
    if channel and channel not in CHANNELS:
        return _fail(
            args,
            2,
            f"{unknown_bind(channel, '')}\nnext: agentself backends --help\n",
            "refused",
            unknown_bind(channel, "") or "unknown channel",
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
    try:
        if argv0:
            executable = str(Path(argv0).resolve())
    except OSError:
        executable = argv0 or sys.executable
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
    path = Path(vault) / "registry.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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


def _missing_host_tool(is_init: bool, vault: Path, args) -> int | None:
    store_name = CHANNELS["store"].default
    if is_init:
        store_name = getattr(args, "store", None) or store_name
    else:
        try:
            cfg = load_config(vault)
            if config_path(vault).is_file():
                identity_id = (cfg.get("identity_id") or "").strip()
                recorded = _registry_store_binding(vault, identity_id)
                if recorded in CHANNELS["store"].names:
                    store_name = recorded
        except IdentityStateError:
            pass
    if shutil.which("age-keygen") is None:
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
    missing = [name for name in tools if shutil.which(name) is None]
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
    tools = {"age-keygen": True}
    bind = bind_of("store", store_name)
    if bind is not None:
        for name in bind.tools:
            tools[name] = True
    return tools


def _tools_next(missing: list[str], store_name: str | None = None) -> str:
    from agentself.internal.host_tools import INSTALLABLE_TOOLS

    installable = set(INSTALLABLE_TOOLS)
    bind = bind_of("store", store_name) if store_name else None
    if bind is not None:
        installable.update(bind.installable_tools)
    if missing and all(name in installable for name in missing):
        return _INSTALL_TOOLS_NEXT
    return _DIAGNOSE_NEXT


def _fail_missing_tool(args, exc: HostToolMissing) -> int:
    missing = [
        part.strip()
        for part in str(exc.tool).replace(" and ", ",").split(",")
        if part.strip()
    ]
    store_name = getattr(args, "store", None) or CHANNELS["store"].default
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
        store_name = CHANNELS["store"].default
        if initialized:
            identity_id = (cfg.get("identity_id") or "").strip()
            recorded = _registry_store_binding(vault, identity_id)
            if recorded in CHANNELS["store"].names:
                store_name = recorded
    except IdentityStateError as exc:
        return _fail(
            args,
            1,
            f"error: {exc}\n",
            "error",
            str(exc),
            nxt=_DIAGNOSE_NEXT,
        )
    python = sys.version.split()[0]
    wallet_backend = email_backend = store_backend = None
    ready = {"wallet": False, "email": False, "store": False}
    if initialized:
        wallet_backend = (cfg.get("wallet_backend") or "").strip() or None
        email_backend = (cfg.get("email_backend") or "").strip() or None
        store_backend = store_name
    problems: list[tuple[str, str]] = []
    if initialized:
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
            payload["ok"] = False
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
    err = unknown_bind(channel, value)
    if err is None:
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
        store = getattr(args, "store", None) or CHANNELS["store"].default
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
        if initialized and not bool(getattr(args, "force", False)):
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
        sys.stdout.write(redact_secrets(text))
        return 0
    except UnboundCaller:
        return _fail(
            args,
            2,
            "refused: not initialized\n",
            "refused",
            "not initialized",
            nxt="agentself init",
        )
    except IdentityStateError as exc:
        return _fail(
            args,
            1,
            f"error: {exc}\n",
            "error",
            str(exc),
            nxt=_DIAGNOSE_NEXT,
        )
    except UnknownBind as exc:
        return _bind_error(args, exc)
    except ValueError:
        return _fail(args, 2, "refused\n", "refused")
    except HostToolMissing as exc:
        return _fail_missing_tool(args, exc)
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

    flagged = (getattr(args, "identity_id", None) or "").strip()
    if flagged:
        return require_safe_token(flagged, "identity id")
    existing = load_config(vault).get("identity_id", "").strip()
    if existing:
        return require_safe_token(existing, "identity id")
    env_id = os.environ.get(ENV_IDENTITY_ID, "").strip()
    if env_id:
        return require_safe_token(env_id, "identity id")
    asked = _ask_identity_name()
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
    changing = any(
        [
            bool(current_id and current_id != identity_id),
            bool(current_wallet and current_wallet != wallet_backend),
            bool(current_email and current_email != email_backend),
            bool(recorded and recorded != store),
        ]
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
            state=(getattr(args, "setup_state", "") or "").strip() or None,
        )
        return _email_connect_result(vault, args, result)
    except UnknownBind as exc:
        return _bind_error(args, exc)
    except UnboundCaller:
        return _fail(
            args,
            2,
            "refused: not initialized\n",
            "refused",
            "not initialized",
            nxt="agentself init",
        )
    except ChannelFailure as exc:
        return _email_connect_channel_fail(args, exc)
    except HostToolMissing as exc:
        return _fail(
            args,
            1,
            f"error: {exc}\n",
            "error",
            str(exc),
            nxt=_INSTALL_TOOLS_NEXT,
        )
    except StoreFailure as exc:
        return _store_fail(args, exc)
    except Exception:
        return _fail(args, 1, "error\n", "error")


def _connect_answers(args) -> tuple[dict[str, str], int | None]:
    do_continue = bool(getattr(args, "do_continue", False))
    state = (getattr(args, "setup_state", "") or "").strip()
    path = (getattr(args, "result_file", "") or "").strip()
    if path and not do_continue:
        return {}, _fail(
            args,
            2,
            "refused: --result-file needs --continue\n",
            "refused",
            "--result-file needs --continue",
            nxt="agentself email connect --help",
        )
    if state and not do_continue:
        return {}, _fail(
            args,
            2,
            "refused: --state needs --continue\n",
            "refused",
            "--state needs --continue",
            nxt="agentself email connect --help",
        )
    if not do_continue:
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
    if (
        status in (SETUP_INPUT_REQUIRED, SETUP_ACTION_REQUIRED, SETUP_PENDING)
        and not _as_json(args)
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    ):
        prompted = _prompt_setup_option(result)
        if prompted is None:
            return _email_setup_pending(args, result, status)
        if prompted:
            option = result.get("option")
            if isinstance(option, dict) and (
                str(option.get("type") or "").strip().lower() == "secret"
                or bool(option.get("sensitive"))
            ):
                sys.stdout.write("Checking the credential...\n")
                setattr(args, "_interactive_email_credential_stored", True)
            try:
                nxt = _client(vault).email_connect(
                    answers=prompted,
                    state=str(result.get("state") or "") or None,
                )
            except ChannelFailure as exc:
                return _email_connect_channel_fail(args, exc)
            except StoreFailure as exc:
                return _store_fail(args, exc)
            except HostToolMissing as exc:
                return _fail(
                    args,
                    1,
                    f"error: {exc}\n",
                    "error",
                    str(exc),
                    nxt=_INSTALL_TOOLS_NEXT,
                )
            except Exception:
                return _fail(args, 1, "error\n", "error")
            return _email_connect_result(vault, args, nxt)
        return _fail(
            args,
            3,
            "nothing entered\n",
            "missing",
            "nothing entered",
            nxt="agentself email connect",
        )
    return _email_setup_pending(args, result, status)


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
    payload: dict[str, object] = {}
    for key in (
        "status",
        "state",
        "option",
        "human_action_required",
        "continue",
        "message",
    ):
        if key in result:
            payload[key] = result[key]
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
    reason = "input required"
    if status == SETUP_ACTION_REQUIRED:
        reason = "human action required"
    elif status == SETUP_PENDING:
        reason = "pending"
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
        client.update(args.name, value)
        if _as_json(args):
            return _emit_ok(args, {"name": args.name})
        return 0
    if verb == "list":
        names = client.list()
        protected = [name for name in names if name in PROTECTED_SECRET_NAMES]
        if _as_json(args):
            return _emit_ok(args, {"names": names, "protected": protected})
        for name in names:
            suffix = " (protected)" if name in PROTECTED_SECRET_NAMES else ""
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
    if name in PROTECTED_SECRET_NAMES and not bool(getattr(args, "unsafe", False)):
        return _fail(
            args,
            2,
            f"refused: {name} is protected\n",
            "refused",
            f"{name} is protected",
            nxt="agentself secret get NAME --unsafe",
        )
    value = client.get(name)
    meta = value_meta(value)
    path = (getattr(args, "to_file", None) or "").strip()
    if getattr(args, "meta", False):
        payload = {"name": name, **meta, "protected": name in PROTECTED_SECRET_NAMES}
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
    sys.stdout.write(value + "\n")
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
    if args.email_command == "receive":
        messages = client.email_receive(message_id=getattr(args, "message_id", None))
        if _as_json(args):
            return _emit_ok(args, {"messages": messages})
        for msg in messages:
            sys.stdout.write(json.dumps(msg) + "\n")
        return 0
    if args.email_command == "list":
        items = client.email_list()
        if _as_json(args):
            return _emit_ok(args, {"messages": items})
        for item in items:
            sys.stdout.write(json.dumps(item) + "\n")
        return 0
    return 1


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
        path = (getattr(args, "from_file", None) or "").strip()
        authorization = (getattr(args, "authorization", None) or "").strip()
        leftover = (getattr(args, "message", None) or "").strip()
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
        asset = client.wallet_send(
            args.to, args.amount, getattr(args, "asset", "") or ""
        )
        if _as_json(args):
            return _emit_ok(
                args, {"to": args.to, "amount": args.amount, "asset": asset}
            )
        return 0
    return 1


def _backup_restore(vault: Path, args) -> int:
    src = vault if args.command == "backup" else Path(args.path)
    dest = Path(args.path) if args.command == "backup" else vault
    try:
        _copy_identity_dir(src, dest, force=bool(getattr(args, "force", False)))
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


def _copy_identity_dir(src: Path, dest: Path, *, force: bool) -> None:
    src = Path(src)
    dest = Path(dest)
    if not src.is_dir():
        raise IdentityStateError("identity directory is missing")
    try:
        src_r = src.resolve()
        dest_r = dest.resolve()
    except OSError as exc:
        raise IdentityStateError("cannot read path") from exc
    if dest_r == src_r:
        raise IdentityStateError("destination is the identity directory")
    try:
        dest_r.relative_to(src_r)
        raise IdentityStateError("destination is inside the identity directory")
    except ValueError:
        pass
    if dest.exists():
        if dest.is_file():
            raise IdentityStateError("destination exists")
        try:
            nonempty = any(dest.iterdir())
        except OSError as exc:
            raise IdentityStateError("cannot read destination") from exc
        if nonempty:
            if not force:
                raise IdentityStateError("destination is not empty")
            shutil.rmtree(dest)
        else:
            dest.rmdir()
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, copy_function=shutil.copy2)


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
        if bool(getattr(args, "_interactive_email_credential_stored", False)):
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
    nxt = "agentself secret create --help"
    if getattr(args, "secret_command", None) == "update":
        nxt = "agentself secret update --help"
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
    argv_value = getattr(args, "value", None)
    path = (getattr(args, "from_file", None) or "").strip()
    if argv_value is not None and path:
        return None, "value and --file"
    if path:
        try:
            return load_value_file(path), None
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
    argv_value = getattr(args, "message", None)
    path = (getattr(args, "from_file", None) or "").strip()
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

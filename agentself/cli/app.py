from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from agentself import __version__
from agentself.bind import public_recipient
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
    CannotSend,
    CannotSign,
    ChannelFailure,
    EmailSendNotReady,
    HostToolMissing,
    MissingHoldName,
    NoGas,
    ProtectedName,
    Refused,
    StoreFailure,
    UnboundCaller,
    UnknownPrincipal,
)
from agentself.internal.format import format_version_error
from agentself.internal.log import NullLog, StreamLog
from agentself.internal.names import require_safe_token
from agentself.local import (
    DEFAULT_IDENTITY,
    VaultStateError,
    bind_local,
    config_path,
    default_vault,
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
    vault = default_vault()

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
        return _start(vault, args)
    if args.command == "email" and getattr(args, "email_command", None) == "connect":
        return _email_connect(vault, args)

    gateway = None
    try:
        gateway = _gateway(vault)
    except UnknownBind as exc:
        return _bind_error(args, exc)
    except VaultStateError as exc:
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
            view = gateway.identity()
            if _as_json(args):
                return _emit_ok(args, _status_json(view, vault))
            sys.stdout.write(format_status(view, vault))
            return 0
        if args.command == "secret":
            return _secret(gateway, args)
        if args.command == "email":
            return _email(gateway, args)
        if args.command == "wallet":
            return _wallet(gateway, args)
    except VaultStateError as exc:
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
            "refused: unbound caller\n",
            "refused",
            "unbound caller",
            nxt="agentself init",
        )
    except ValueError:
        return _fail(args, 2, "refused\n", "refused")
    except UnknownPrincipal:
        return _fail(
            args, 2, "refused: unknown principal\n", "refused", "unknown principal"
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
    except CannotSign:
        return _fail(
            args,
            2,
            "refused: backend cannot sign\n",
            "refused",
            "backend cannot sign",
        )
    except CannotSend as exc:
        msg = str(exc)
        if msg in ("need USDC", "need USD"):
            return _fail(args, 2, f"refused: {msg}\n", "refused", msg)
        return _fail(
            args,
            2,
            "refused: backend cannot send\n",
            "refused",
            "backend cannot send",
        )
    except NoGas:
        return _fail(args, 2, "refused: EOA has no ETH\n", "refused", "EOA has no ETH")
    except MissingHoldName:
        return _fail(args, 3, "missing\n", "missing", nxt="agentself secret list")
    except EmailSendNotReady as exc:
        return _fail(
            args,
            1,
            f"error: {exc}\n",
            "error",
            getattr(exc, "reason", None) or "not_ready",
            nxt="agentself backends email",
        )
    except HostToolMissing as exc:
        return _fail(
            args,
            1,
            f"error: {exc}\n",
            "error",
            str(exc),
            nxt=_INSTALL_TOOLS_NEXT,
        )
    except ChannelFailure as exc:
        reason = getattr(exc, "reason", None) or "error"
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
    dest_dir = (
        Path.home() if getattr(args, "global_install", False) else Path.cwd()
    ) / rel
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


def _print_version(as_json: bool) -> int:
    if as_json:
        return _emit_ok(
            argparse.Namespace(as_json=True),
            {"version": __version__, "cli": CLI_SCHEMA_VERSION},
        )
    sys.stdout.write(f"agentself {__version__}\n")
    return 0


def _registry_store_binding(vault: Path, principal_id: str) -> str | None:
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
        raise VaultStateError(err)
    if not principal_id:
        return None
    identities = data.get("identities")
    if not isinstance(identities, dict):
        return None
    raw = identities.get(principal_id)
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
        if store_name == "pass":
            missing = [name for name in ("gpg", "pass") if shutil.which(name) is None]
            if missing:
                reason = " and ".join(missing) + " not on PATH"
                return _fail(
                    args,
                    1,
                    f"error: {reason}\n",
                    "error",
                    reason,
                    nxt=_INSTALL_TOOLS_NEXT,
                )
    if not is_init:
        try:
            cfg = load_config(vault)
            if config_path(vault).is_file():
                principal_id = (cfg.get("identity_id") or "").strip()
                recorded = _registry_store_binding(vault, principal_id)
                if recorded in CHANNELS["store"].names:
                    store_name = recorded
        except VaultStateError:
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
    if store_name == "pass":
        missing = [name for name in ("gpg", "pass") if shutil.which(name) is None]
        if missing:
            reason = " and ".join(missing) + " not on PATH"
            return _fail(
                args,
                1,
                f"error: {reason}\n",
                "error",
                reason,
                nxt=_INSTALL_TOOLS_NEXT,
            )
        return None
    if shutil.which(store_name) is None:
        reason = f"{store_name} not on PATH"
        return _fail(
            args,
            1,
            f"error: {reason}\n",
            "error",
            reason,
            nxt=_INSTALL_TOOLS_NEXT,
        )
    return None


def _diagnose(vault: Path, args) -> int:
    try:
        cfg = load_config(vault)
        initialized = config_path(vault).is_file()
        store_name = CHANNELS["store"].default
        if initialized:
            principal_id = (cfg.get("identity_id") or "").strip()
            recorded = _registry_store_binding(vault, principal_id)
            if recorded in CHANNELS["store"].names:
                store_name = recorded
    except VaultStateError as exc:
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
    payload = {
        "ok": not problems,
        "initialized": initialized,
        "ready": ready,
        "version": __version__,
        "python": python,
        "vault": str(vault),
        "tools": {"age-keygen": True, store_name: True},
        "wallet_backend": wallet_backend,
        "email_backend": email_backend,
        "store_backend": store_backend,
    }
    if problems:
        payload["problems"] = [item[0] for item in problems]
        payload["error"] = "error"
        payload["reason"] = problems[0][0]
        payload["next"] = problems[0][1]
    if _as_json(args):
        if problems:
            sys.stderr.write(redact_secrets(json.dumps(payload)) + "\n")
            return 1
        return _emit_ok(args, payload)
    lines = [
        f"version: {__version__}",
        f"python: {python}",
        f"vault: {vault}",
        "age-keygen: ok",
        f"{store_name}: ok",
        f"initialized: {'yes' if initialized else 'no'}",
    ]
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
        gateway = _gateway(vault)
        names = gateway.list()
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
    wallet_name = (cfg.get("wallet_backend") or "").strip()
    wallet = bind_of("wallet", wallet_name)
    if wallet is None or (wallet.live and wallet.custody == "eoa-key"):
        if "wallet.key" not in names:
            problems.append(("wallet.key is missing", "agentself init"))
            return problems, ready
        try:
            gateway.reveal("wallet.key")
        except StoreFailure as exc:
            problems.append((_store_reason(exc), "agentself secret get wallet.key"))
            return problems, ready
        except MissingHoldName:
            problems.append(("wallet.key is missing", "agentself init"))
            return problems, ready
        except Exception:
            problems.append(("identity is not usable", "agentself init"))
            return problems, ready
        ready["wallet"] = True
    else:
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


def _gateway(vault: Path, **compose_kw):
    return _compose()(
        vault,
        log=_cli_log(),
        mail_domain=mail_domain(vault),
        bind=lambda: bind_local(vault),
        **compose_kw,
    )


def _start(vault: Path, args) -> int:
    try:
        require_supported_formats(vault)
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
        identity_id = _start_identity_id(vault, args)
        key = ensure_age_key(vault, identity_id, store)
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
        gateway = _gateway(
            vault,
            email_backend=email_backend,
            wallet_backend=wallet_backend,
        )
        gateway.enroll(store)
        addr = gateway.wallet_address()
        merge_config(vault, {**identity_fields, **backend_fields})
        if _as_json(args):
            return _emit_ok(
                args,
                {
                    "id": identity_id,
                    "recipient": recipient,
                    "address": addr,
                    "usdc": addr,
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
            "refused: unbound caller\n",
            "refused",
            "unbound caller",
            nxt="agentself init",
        )
    except VaultStateError as exc:
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


def _start_identity_id(vault: Path, args) -> str:
    from agentself.host import ENV_IDENTITY_ID

    flagged = (getattr(args, "identity_id", None) or "").strip()
    if flagged:
        return require_safe_token(flagged, "principal id")
    existing = load_config(vault).get("identity_id", "").strip()
    if existing:
        return require_safe_token(existing, "principal id")
    env_id = os.environ.get(ENV_IDENTITY_ID, "").strip()
    if env_id:
        return require_safe_token(env_id, "principal id")
    asked = _ask_identity_name()
    if asked:
        return require_safe_token(asked, "principal id")
    return DEFAULT_IDENTITY


def _ask_identity_name() -> str:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return ""
    try:
        return input("Name this identity [agent]: ").strip()
    except EOFError:
        return ""


def _email_connect(vault: Path, args) -> int:
    try:
        desc = _gateway(vault).email_connect()
    except UnknownBind as exc:
        return _bind_error(args, exc)
    except UnboundCaller:
        return _fail(
            args,
            2,
            "refused: unbound caller\n",
            "refused",
            "unbound caller",
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
    email = desc if isinstance(desc, dict) else {}
    addr = ""
    if email.get("owned_address") and email.get("address"):
        addr = str(email["address"])
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


def _email_connect_channel_fail(args, exc: ChannelFailure) -> int:
    reason = getattr(exc, "reason", None) or "error"
    if reason == "no_token":
        return _fail(
            args,
            3,
            "need email.send.token\n",
            "missing",
            "need email.send.token",
            nxt="agentself secret create email.send.token",
        )
    if reason == "need_address":
        return _fail(
            args,
            3,
            "need email.address\n",
            "missing",
            "need email.address",
            nxt="agentself secret create email.address",
        )
    return _fail(
        args,
        1,
        f"error: {reason}\n",
        "error",
        reason,
        nxt="agentself backends email",
    )


def _secret(gateway, args) -> int:
    verb = args.secret_command
    if verb == "create":
        value, err = _secret_from_args(args)
        if err is not None:
            return _secret_value_error(args, err)
        gateway.seal(args.name, value)
        if _as_json(args):
            return _emit_ok(args, {"name": args.name})
        return 0
    if verb == "get":
        value = gateway.reveal(args.name)
        if _as_json(args):
            return _emit_ok(args, {"name": args.name, "value": value}, redact=False)
        sys.stdout.write(value + "\n")
        return 0
    if verb == "update":
        value, err = _secret_from_args(args)
        if err is not None:
            return _secret_value_error(args, err)
        gateway.replace(args.name, value)
        if _as_json(args):
            return _emit_ok(args, {"name": args.name})
        return 0
    if verb == "list":
        names = gateway.list()
        if _as_json(args):
            return _emit_ok(args, {"names": names})
        for name in names:
            sys.stdout.write(name + "\n")
        return 0
    if verb == "delete":
        gateway.delete(args.name)
        if _as_json(args):
            return _emit_ok(args, {"name": args.name})
        sys.stdout.write("ok\n")
        return 0
    return 1


def _email(gateway, args) -> int:
    if args.email_command == "show":
        email = gateway.identity().get("email")
        email = email if isinstance(email, dict) else {}
        if _as_json(args):
            return _emit_ok(args, email)
        if email.get("owned_address") and email.get("address"):
            sys.stdout.write(str(email["address"]) + "\n")
        else:
            sys.stdout.write("not configured\n")
        return 0
    if args.email_command == "send":
        gateway.email_send(args.to, args.subject, args.body)
        if _as_json(args):
            return _emit_ok(args, {"to": args.to, "subject": args.subject})
        return 0
    if args.email_command == "receive":
        messages = gateway.email_recv(message_id=getattr(args, "message_id", None))
        if _as_json(args):
            return _emit_ok(args, {"messages": messages})
        for msg in messages:
            sys.stdout.write(json.dumps(msg) + "\n")
        return 0
    if args.email_command == "list":
        items = gateway.email_list()
        if _as_json(args):
            return _emit_ok(args, {"messages": items})
        for item in items:
            sys.stdout.write(json.dumps(item) + "\n")
        return 0
    return 1


def _wallet(gateway, args) -> int:
    if args.wallet_command in ("show", "address"):
        addr = gateway.wallet_address()
        if _as_json(args):
            return _emit_ok(args, {"address": addr})
        sys.stdout.write(addr + "\n")
        return 0
    if args.wallet_command == "balance":
        bal = gateway.wallet_balance()
        if _as_json(args):
            return _emit_ok(args, dict(bal))
        sys.stdout.write(json.dumps(bal) + "\n")
        return 0
    if args.wallet_command == "authorize":
        token = gateway.wallet_sign(args.message)
        if _as_json(args):
            return _emit_ok(args, {"authorization": token, "signature": token})
        sys.stdout.write(token + "\n")
        return 0
    if args.wallet_command == "send":
        asset = gateway.wallet_send(
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
    except VaultStateError as exc:
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
        raise VaultStateError("identity directory is missing")
    try:
        src_r = src.resolve()
        dest_r = dest.resolve()
    except OSError as exc:
        raise VaultStateError("cannot read path") from exc
    if dest_r == src_r:
        raise VaultStateError("destination is the identity directory")
    try:
        dest_r.relative_to(src_r)
        raise VaultStateError("destination is inside the identity directory")
    except ValueError:
        pass
    if dest.exists():
        if dest.is_file():
            raise VaultStateError("destination exists")
        try:
            nonempty = any(dest.iterdir())
        except OSError as exc:
            raise VaultStateError("cannot read destination") from exc
        if nonempty and not force:
            raise VaultStateError("destination is not empty")
        if nonempty and force:
            shutil.rmtree(dest)
        elif not nonempty:
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
        return _emit_ok(args, {"address": addr})
    if addr:
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
    name = getattr(exc, "name", None)
    if name:
        return f"cannot read {name}"
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
    verb = getattr(args, "secret_command", None)
    if verb == "update":
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
        if verb in ("get", "delete", "list"):
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
        sys.stderr.write(json.dumps(payload) + "\n")
    else:
        sys.stderr.write(human)
    return exit_code


def _status_json(view: dict[str, object], vault: Path) -> dict[str, object]:
    raw_wallet = view.get("wallet")
    wallet: dict[str, object] = raw_wallet if isinstance(raw_wallet, dict) else {}
    addr = wallet.get("address")
    return {
        "id": view.get("id"),
        "recipient": view.get("recipient"),
        "address": addr,
        "usdc": addr,
        "wallet_backend": view.get("wallet_backend"),
        "email_backend": view.get("email_backend"),
        "vault": str(vault),
    }


def _secret_from_args(args) -> tuple[str | None, str | None]:
    argv_value = getattr(args, "value", None)
    path = (getattr(args, "from_file", None) or "").strip()
    if argv_value is not None and path:
        return None, "value and --file"
    if path:
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError:
            return None, "file"
        return raw.removesuffix("\n"), None
    if argv_value is not None:
        return argv_value, None
    if sys.stdin.isatty():
        return None, "need a value"
    return sys.stdin.read().removesuffix("\n"), None


def run() -> None:
    raise SystemExit(main())

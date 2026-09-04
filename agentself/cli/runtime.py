from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from agentself.cli.io import load_value_file
from agentself.cli.outcomes import CliFailure
from agentself.host import (
    CHANNELS,
    ENV_ETH_RPC_URL,
    ENV_IDENTITY_DIR,
    ENV_LOG,
    UnknownBind,
    bind_of,
    unknown_bind,
)
from agentself.internal.custody.errors import HostToolMissing, StoreFailure
from agentself.internal.files import have_host_tool
from agentself.internal.format import format_version_error, load_json_file
from agentself.internal.log import NullLog, StreamLog
from agentself.internal.names import require_safe_token
from agentself.local import (
    IdentityStateError,
    bind_local,
    config_path,
    load_config,
    mail_domain,
    redact_secrets,
)

INSTALL_TOOLS_NEXT = "agentself install --tools"
DIAGNOSE_NEXT = "agentself diagnose"
PASS_TOOLS_REASON = "gpg and pass are host packages, not agentself tools"
PASS_TOOLS_NEXT = "agentself backends store"
FUND_ETH_NEXT = "fund ETH"
TYPED_AUTHORIZE_NEXT = "agentself wallet authorize --help"
RPC_URL_NEXT = f"set {ENV_ETH_RPC_URL}"
IDENTITY_DIR_INIT_NEXT = "agentself --identity-dir PATH init"


def fail(
    args,
    exit_code: int,
    error: str,
    reason: str | None = None,
    nxt: str | None = None,
    extra: dict[str, object] | None = None,
) -> CliFailure:
    if reason is not None:
        reason = redact_secrets(reason)
    else:
        reason = error
    if nxt is None:
        nxt = default_json_next(args, error)
    return CliFailure(exit_code, error, reason, nxt, extra)


def resource_name_error(args, name: str, resource: str, nxt: str) -> CliFailure | None:
    try:
        require_safe_token(name, f"{resource} name")
    except (TypeError, ValueError):
        return fail(args, 2, "refused", f"invalid {resource} name", nxt=nxt)
    return None


def identity_dir_flag(args) -> str:
    return str(getattr(args, "identity_dir", None) or "").strip()


def with_identity_dir(args, command: str) -> str:
    flagged = identity_dir_flag(args)
    if flagged:
        return f"agentself --identity-dir {flagged} {command}"
    return f"agentself {command}"


def init_next(args) -> str:
    flagged = identity_dir_flag(args)
    if flagged:
        return f"agentself --identity-dir {flagged} init"
    if os.environ.get(ENV_IDENTITY_DIR, "").strip():
        return "agentself init"
    return IDENTITY_DIR_INIT_NEXT


def channel_next(args) -> str:
    command = getattr(args, "command", None)
    if command == "email":
        return "agentself backends email"
    if command == "wallet":
        return "agentself backends wallet"
    if command == "secret":
        return "agentself secret --help"
    if command == "note":
        return "agentself note --help"
    return "agentself --help"


def wallet_failure_next(args, reason: str) -> str:
    if reason in {"no_gas", "need_gas"}:
        return FUND_ETH_NEXT
    if reason == "rpc":
        return RPC_URL_NEXT
    if reason in {"cannot_authorize", "backend cannot authorize"}:
        return TYPED_AUTHORIZE_NEXT
    if reason == "insufficient_asset":
        return "agentself wallet balance"
    return channel_next(args)


def default_json_next(args, error: str) -> str:
    if error == "missing":
        note_verb = getattr(args, "note_command", None)
        if note_verb == "set":
            return "agentself note set --help"
        if note_verb in ("get", "delete", "list", "exists"):
            return "agentself note list"
        verb = getattr(args, "secret_command", None)
        if verb == "create":
            return "agentself secret create --help"
        if verb == "update":
            return "agentself secret update --help"
        if verb in ("get", "run", "delete", "list", "exists"):
            return "agentself secret list"
        return "agentself --help"
    if error == "refused":
        return channel_next(args)
    return DIAGNOSE_NEXT


def bind_error(args, exc: UnknownBind) -> CliFailure:
    return fail(
        args,
        2,
        "refused",
        str(exc),
        nxt=f"agentself backends {exc.channel}",
    )


def identity_fail(args, exc: IdentityStateError) -> CliFailure:
    return fail(args, 1, "error", str(exc), nxt=DIAGNOSE_NEXT)


def not_initialized(args) -> CliFailure:
    return fail(args, 2, "refused", "not initialized", nxt=init_next(args))


def identity_busy(args) -> CliFailure:
    return fail(args, 1, "error", "identity directory busy", nxt=DIAGNOSE_NEXT)


def require_bind(args, channel: str, value: str) -> CliFailure | None:
    if unknown_bind(channel, value) is None:
        return None
    return bind_error(args, UnknownBind(channel, value))


def store_reason(exc: StoreFailure) -> str:
    if exc.name:
        return f"cannot read {exc.name}"
    return str(exc).strip() or "store error"


def store_fail(args, exc: StoreFailure) -> CliFailure:
    return fail(args, 1, "error", store_reason(exc), nxt=DIAGNOSE_NEXT)


def cli_log():
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


def client(vault: Path, **compose_kw):
    return _compose()(
        vault,
        log=cli_log(),
        mail_domain=mail_domain(vault),
        bind=lambda: bind_local(vault),
        **compose_kw,
    )


def runtime_paths() -> dict[str, str]:
    package = str(Path(__file__).resolve().parent.parent)
    argv0 = str(sys.argv[0] or "")
    executable = argv0
    if argv0:
        try:
            executable = str(Path(argv0).resolve())
        except OSError:
            pass
    return {"package": package, "executable": executable}


def registry_store_binding(vault: Path, identity_id: str) -> str | None:
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


def store_from_registry(vault: Path, cfg: dict[str, str]) -> str | None:
    if not config_path(vault).is_file():
        return None
    identity_id = (cfg.get("identity_id") or "").strip()
    recorded = registry_store_binding(vault, identity_id)
    if recorded in CHANNELS["store"].names:
        return recorded
    return None


def diagnose_tools(store_name: str) -> dict[str, bool]:
    bind = bind_of("store", store_name)
    names = ("age-keygen",) + (bind.tools if bind is not None else ())
    return {name: have_host_tool(name) for name in names}


def tools_next(missing: list[str], store_name: str | None = None) -> str:
    from agentself.internal.host_tools import INSTALLABLE_TOOLS

    installable = set(INSTALLABLE_TOOLS)
    bind = bind_of("store", store_name) if store_name else None
    if bind is not None:
        installable.update(bind.installable_tools)
    if missing and all(name in installable for name in missing):
        return INSTALL_TOOLS_NEXT
    if store_name == "pass" and missing and set(missing) <= {"gpg", "pass"}:
        return PASS_TOOLS_NEXT
    return DIAGNOSE_NEXT


def fail_missing_tool(
    args, exc: HostToolMissing, vault: Path | None = None
) -> CliFailure:
    missing = [
        part.strip()
        for part in str(exc.tool).replace(" and ", ",").split(",")
        if part.strip()
    ]
    store_name = getattr(args, "store", None) or None
    if not store_name and vault is not None:
        try:
            store_name = store_from_registry(vault, load_config(vault))
        except IdentityStateError:
            pass
    if not store_name:
        store_name = CHANNELS["store"].default
    return fail(args, 1, "error", str(exc), nxt=tools_next(missing, store_name))


def missing_host_tool(is_init: bool, vault: Path, args) -> CliFailure | None:
    store_name = CHANNELS["store"].default
    if is_init:
        store_name = getattr(args, "store", None) or store_name
    else:
        try:
            store_name = store_from_registry(vault, load_config(vault)) or store_name
        except IdentityStateError:
            pass
    if not have_host_tool("age-keygen"):
        return fail(
            args,
            1,
            "error",
            "age not on PATH",
            nxt=INSTALL_TOOLS_NEXT,
        )
    bind = bind_of("store", store_name)
    tools = bind.tools if bind is not None else ()
    missing = [name for name in tools if not have_host_tool(name)]
    if not missing:
        return None
    nxt = tools_next(missing, store_name)
    if nxt == PASS_TOOLS_NEXT:
        return fail(args, 1, "error", PASS_TOOLS_REASON, nxt=nxt)
    reason = " and ".join(missing) + " not on PATH"
    return fail(args, 1, "error", reason, nxt=nxt)


def status_json(view: dict[str, object], vault: Path) -> dict[str, object]:
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


def value_source_error(args, err: str, nxt: str) -> CliFailure:
    if err == "need a value":
        return fail(args, 3, "missing", err, nxt=nxt)
    if err == "file":
        return fail(args, 1, "error", err, nxt=nxt)
    return fail(args, 2, "refused", err, nxt=nxt)


def secret_value_error(args, err: str) -> CliFailure:
    nxt = (
        "agentself secret update --help"
        if getattr(args, "secret_command", None) == "update"
        else "agentself secret create --help"
    )
    return value_source_error(args, err, nxt)


def value_from_file_or_arg(
    argv_value: str | None,
    path: str,
    *,
    both_error: str,
    strip_newline: bool,
    empty_is_missing: bool,
) -> tuple[str | None, str | None]:
    path = (path or "").strip()
    provided = argv_value is not None and not (
        empty_is_missing and str(argv_value) == ""
    )
    if provided and path:
        return None, both_error
    if path:
        try:
            return load_value_file(path, strip_newline=strip_newline), None
        except (OSError, UnicodeDecodeError):
            return None, "file"
    if provided:
        return str(argv_value), None
    return None, "need a value"


def secret_from_args(args) -> tuple[str | None, str | None]:
    return value_from_file_or_arg(
        args.value,
        getattr(args, "from_file", "") or "",
        both_error="value and --file",
        strip_newline=False,
        empty_is_missing=False,
    )


def note_from_args(args) -> tuple[str | None, str | None]:
    return value_from_file_or_arg(
        args.value,
        getattr(args, "from_file", "") or "",
        both_error="value and --file",
        strip_newline=False,
        empty_is_missing=False,
    )


def message_from_args(args) -> tuple[str | None, str | None]:
    return value_from_file_or_arg(
        args.message,
        getattr(args, "from_file", "") or "",
        both_error="message and --file",
        strip_newline=False,
        empty_is_missing=True,
    )

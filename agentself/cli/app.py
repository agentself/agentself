from __future__ import annotations

import argparse
import importlib
import json
import sys
from typing import cast

from agentself import __version__
from agentself.cli.outcomes import CliFailure, CliOutcome, CliRaw, CliSuccess
from agentself.cli.parser import _parser
from agentself.cli.registry import (
    CommandSpec,
    command_recovery,
    commands_payload,
    spec_for,
)
from agentself.cli.runtime import (
    INSTALL_TOOLS_NEXT,
    bind_error,
    channel_next,
    cli_log,
    fail,
    fail_missing_tool,
    identity_busy,
    identity_fail,
    missing_host_tool,
    not_initialized,
    runtime_paths,
    store_fail,
    wallet_failure_next,
)
from agentself.cli.types import CommandArguments, Handler
from agentself.host import UnknownBind
from agentself.internal.custody.errors import (
    CannotAuthorize,
    CannotSend,
    ChannelFailure,
    EmailSendNotReady,
    HostToolMissing,
    MissingNote,
    MissingSecret,
    NoGas,
    ProtectedName,
    Refused,
    StoreFailure,
    UnboundCaller,
    UnknownIdentity,
)
from agentself.internal.files import IdentityBusy
from agentself.internal.log import record_diagnostic
from agentself.internal.next import attach_next
from agentself.internal.text import utf8_bytes
from agentself.local import (
    IdentityStateError,
    config_path,
    redact_secrets,
    resolve_identity_dir,
)

CLI_SCHEMA_VERSION = 2
_SKIP_HOST_TOOLS = {
    ("install",),
    ("backends",),
    ("commands",),
    ("backup",),
    ("restore",),
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


def _flag_value(argv: list[str], name: str) -> str | None:
    """Last ``--name VALUE`` or ``--name=VALUE`` before ``--``. Empty is unset.

    Subcommand parents reset the same dest to default when the flag appears
    before the command path, so argv is the source of truth.
    """

    found: str | None = None
    prefix = name + "="
    for index, token in enumerate(argv):
        if token == "--":
            break
        if token == name:
            if index + 1 < len(argv):
                found = argv[index + 1]
            continue
        if token.startswith(prefix):
            found = token[len(prefix) :]
    if found is None or not found.strip():
        return None
    return found


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if _has_flag(raw, "--machine"):
        return _render(
            fail(
                argparse.Namespace(),
                2,
                "refused",
                "unrecognized arguments: --machine",
                nxt="agentself --help",
            )
        )
    if _has_flag(raw, "--version"):
        return _render(_version())
    as_raw = _has_flag(raw, "--raw")
    recovery = command_recovery(raw)
    if recovery is not None:
        reason, recovery_next = recovery
        return _render(
            fail(
                argparse.Namespace(as_raw=as_raw),
                2,
                "refused",
                reason,
                nxt=recovery_next,
            )
        )
    parser = _parser()
    try:
        args = cast(CommandArguments, parser.parse_args(raw))
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else int(code)
    args.as_raw = bool(getattr(args, "as_raw", False) or as_raw)
    flagged_dir = _flag_value(raw, "--identity-dir")
    if flagged_dir:
        args.identity_dir = flagged_dir
    vault = resolve_identity_dir(flagged_dir or getattr(args, "identity_dir", None))
    path = _command_path(args)
    spec = spec_for(path)
    if spec is None or spec.handler is None:
        return _render(
            fail(args, 2, "refused", "unknown command", nxt="agentself --help")
        )
    raw_error = _raw_conflict(args, spec)
    if raw_error is not None:
        return _render(raw_error)
    if spec.path not in _SKIP_HOST_TOOLS:
        from agentself.internal.host_tools import HostToolError, ensure_host_tools

        try:
            ensure_host_tools(fetch=False)
        except HostToolError as exc:
            return _render(fail(args, 1, "error", str(exc), nxt=INSTALL_TOOLS_NEXT))
        if spec.path in (("init",), ("diagnose",)):
            missing = missing_host_tool(spec.path == ("init",), vault, args)
            if missing is not None:
                if spec.path == ("diagnose",) and missing.next != INSTALL_TOOLS_NEXT:
                    missing = None
                if missing is not None:
                    return _render(missing)
    outcome: CliOutcome
    try:
        if spec.path == ("commands",):
            email_next = None
            if config_path(vault).is_file():
                from agentself.cli.commands.catalog import _email_catalog_next

                email_next = _email_catalog_next(vault)
            outcome = CliSuccess(commands_payload(email_next=email_next))
        else:
            handler = _load_handler(spec)
            outcome = handler(args, vault)
    except IdentityStateError as exc:
        outcome = identity_fail(args, exc)
    except IdentityBusy:
        outcome = identity_busy(args)
    except UnboundCaller:
        outcome = not_initialized(args)
    except UnknownBind as exc:
        outcome = bind_error(args, exc)
    except ValueError:
        outcome = fail(args, 2, "refused")
    except UnknownIdentity:
        outcome = fail(args, 2, "refused", "unknown identity")
    except ProtectedName as exc:
        outcome = fail(args, 2, "refused", str(exc), nxt="agentself secret list")
    except Refused as exc:
        detail = str(exc).strip() or "refused"
        if detail == "refused":
            outcome = fail(args, 2, "refused")
        else:
            nxt = "agentself email list" if detail == "unknown mail ref" else None
            outcome = fail(args, 2, "refused", detail, nxt=nxt)
    except CannotAuthorize:
        outcome = fail(
            args,
            2,
            "refused",
            "typed encoding required",
            nxt=wallet_failure_next(args, "cannot_authorize"),
        )
    except CannotSend as exc:
        reason = exc.reason or "cannot_send"
        outcome = fail(
            args,
            2,
            "refused",
            reason,
            nxt=wallet_failure_next(args, reason),
        )
    except NoGas as exc:
        reason = exc.reason or "no_gas"
        outcome = fail(
            args,
            2,
            "refused",
            reason,
            nxt=wallet_failure_next(args, reason),
        )
    except MissingSecret:
        outcome = fail(args, 3, "missing", nxt="agentself secret list")
    except MissingNote:
        outcome = fail(args, 3, "missing", nxt="agentself note list")
    except EmailSendNotReady as exc:
        outcome = fail(args, 1, "error", exc.reason, nxt="agentself backends email")
    except HostToolMissing as exc:
        outcome = fail_missing_tool(args, exc, vault)
    except ChannelFailure as exc:
        reason = exc.reason
        if reason == "busy" or reason == "identity directory busy":
            outcome = identity_busy(args)
        elif getattr(args, "command", None) == "wallet":
            outcome = fail(
                args,
                1,
                "error",
                reason,
                nxt=wallet_failure_next(args, reason),
            )
        else:
            outcome = fail(args, 1, "error", reason, nxt=channel_next(args))
    except StoreFailure as exc:
        outcome = store_fail(args, exc)
    except FileNotFoundError as exc:
        detail = str(exc).strip()
        if detail.endswith("not on PATH"):
            nxt = INSTALL_TOOLS_NEXT if "age" in detail or "sops" in detail else None
            outcome = fail(args, 1, "error", detail, nxt=nxt)
        else:
            outcome = fail(args, 1, "error")
    except Exception as exc:
        record_diagnostic(cli_log(), ":".join(spec.path), exc)
        outcome = fail(args, 1, "error")
    return _render(outcome)


def _command_path(args: CommandArguments) -> tuple[str, ...]:
    command = getattr(args, "command", None)
    if not command:
        return ("show",)
    nested = getattr(args, f"{command}_command", None)
    if nested:
        return (command, nested)
    return (command,)


def _load_handler(spec: CommandSpec) -> Handler:
    if spec.handler is None:
        raise TypeError(f"command {spec.path!r} has no handler")
    module_name, _, func_name = spec.handler.partition(":")
    if not module_name or not func_name:
        raise TypeError(f"invalid handler reference for {spec.path!r}")
    module = importlib.import_module(module_name)
    loaded = getattr(module, func_name, None)
    if not callable(loaded):
        raise TypeError(f"handler {spec.handler!r} is not callable")
    return cast(Handler, loaded)


def _raw_conflict(args: CommandArguments, spec: CommandSpec) -> CliFailure | None:
    if not getattr(args, "as_raw", False):
        return None
    if not spec.raw:
        return fail(
            args,
            2,
            "refused",
            "--raw is not supported",
            nxt="agentself --help",
        )
    to_file = (getattr(args, "to_file", None) or "").strip()
    body_file = (getattr(args, "body_file", None) or "").strip()
    out_file = (getattr(args, "out_file", None) or "").strip()
    if to_file or body_file:
        return fail(
            args,
            2,
            "refused",
            "--raw cannot be used with --file",
            nxt="agentself --help",
        )
    if out_file:
        return fail(
            args,
            2,
            "refused",
            "--raw cannot be used with --out",
            nxt="agentself --help",
        )
    if getattr(args, "meta", False):
        return fail(
            args,
            2,
            "refused",
            "--raw cannot be used with --meta",
            nxt="agentself --help",
        )
    return None


def _version() -> CliSuccess:
    return CliSuccess(
        {
            "version": __version__,
            "cli": CLI_SCHEMA_VERSION,
            **runtime_paths(),
        }
    )


def _write_raw(data: str) -> None:
    raw = utf8_bytes(data)
    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        try:
            buf.write(raw)
        except (AttributeError, OSError, TypeError, ValueError):
            sys.stdout.write(data)
            return
        try:
            buf.flush()
        except (AttributeError, OSError, TypeError, ValueError):
            return
        return
    sys.stdout.write(data)


def _render(outcome: CliOutcome) -> int:
    if isinstance(outcome, CliRaw):
        _write_raw(outcome.data)
        return 0
    if isinstance(outcome, CliSuccess):
        body = {
            "ok": True,
            **{key: value for key, value in outcome.payload.items() if key != "ok"},
        }
        nxt = body.get("next")
        if isinstance(nxt, str) and "_next" not in body:
            attach_next(body, nxt)
        text = json.dumps(body)
        if outcome.redact:
            text = redact_secrets(text)
        sys.stdout.write(text + "\n")
        return 0
    payload: dict[str, object] = {
        "ok": False,
        "error": outcome.error,
        "reason": outcome.reason,
        "next": outcome.next,
    }
    if outcome.extra:
        for key, value in outcome.extra.items():
            if key not in payload:
                payload[key] = value
    if "_next" not in payload:
        attach_next(payload, outcome.next)
    sys.stdout.write(redact_secrets(json.dumps(payload)) + "\n")
    return outcome.exit_code


def run() -> None:
    raise SystemExit(main())

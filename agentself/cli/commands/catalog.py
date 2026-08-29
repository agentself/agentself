from __future__ import annotations

from pathlib import Path

from agentself.cli.outcomes import CliOutcome, CliSuccess
from agentself.cli.registry import command_verbs, commands_payload
from agentself.cli.runtime import client, fail
from agentself.host import CHANNELS, backends_payload, unknown_bind
from agentself.local import config_path


def _email_catalog_next(vault: Path) -> str | None:
    if not config_path(vault).is_file():
        return None
    try:
        view = client(vault).identity().get("email")
    except Exception:
        return None
    email = view if isinstance(view, dict) else {}
    if email.get("owned_address") and email.get("address"):
        return "agentself email receive"
    return None


def list_commands(_args, vault: Path) -> CliOutcome:
    return CliSuccess(commands_payload(email_next=_email_catalog_next(vault)))


def list_backends(args, vault: Path) -> CliOutcome:
    channel = (args.channel or "").strip() or None
    backend = (getattr(args, "backend", None) or "").strip() or None
    if backend and not channel:
        return fail(
            args,
            2,
            "refused",
            "unknown channel",
            nxt="agentself backends --help",
        )
    if channel and channel not in CHANNELS:
        err = unknown_bind(channel, "") or "unknown channel"
        return fail(args, 2, "refused", err, nxt="agentself backends --help")
    if backend:
        bind_err = unknown_bind(channel or "", backend)
        if bind_err:
            return fail(
                args,
                2,
                "refused",
                bind_err,
                nxt=f"agentself backends {channel}",
            )
    email_next = _email_catalog_next(vault)
    overrides = {"email": email_next} if email_next else None
    return CliSuccess(
        backends_payload(command_verbs(), channel, backend, next_overrides=overrides)
    )

from __future__ import annotations

from pathlib import Path

from agentself.cli.outcomes import CliOutcome, CliSuccess
from agentself.cli.registry import commands_payload
from agentself.cli.runtime import fail
from agentself.host import CHANNELS, backends_payload, unknown_bind


def list_commands(_args, _vault: Path) -> CliOutcome:
    return CliSuccess(commands_payload())


def list_backends(args, _vault: Path) -> CliOutcome:
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
    return CliSuccess(backends_payload(channel, backend))

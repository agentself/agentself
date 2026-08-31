from __future__ import annotations

from pathlib import Path

from agentself.cli.outcomes import CliOutcome, CliRaw, CliSuccess
from agentself.cli.runtime import (
    client,
    fail,
    note_from_args,
    resource_name_error,
    value_source_error,
)


def set_note(args, vault: Path) -> CliOutcome:
    invalid = resource_name_error(args, args.name, "note", "agentself note set --help")
    if invalid is not None:
        return invalid
    value, err = note_from_args(args)
    if err is not None or value is None:
        return value_source_error(
            args,
            err or "need a value",
            "agentself note set --help",
        )
    status = client(vault).note_set(args.name, value)
    return CliSuccess({"name": args.name, "status": status}, redact=False)


def get_note(args, vault: Path) -> CliOutcome:
    invalid = resource_name_error(args, args.name, "note", "agentself note get --help")
    if invalid is not None:
        return invalid
    value = client(vault).note_get(args.name)
    if getattr(args, "as_raw", False):
        return CliRaw(value)
    return CliSuccess({"name": args.name, "value": value}, redact=False)


def list_notes(args, vault: Path) -> CliOutcome:
    return CliSuccess({"names": client(vault).note_list()}, redact=False)


def delete_note(args, vault: Path) -> CliOutcome:
    invalid = resource_name_error(
        args, args.name, "note", "agentself note delete --help"
    )
    if invalid is not None:
        return invalid
    client(vault).note_delete(args.name)
    return CliSuccess({"name": args.name}, redact=False)


def note_exists(args, vault: Path) -> CliOutcome:
    invalid = resource_name_error(
        args, args.name, "note", "agentself note exists --help"
    )
    if invalid is not None:
        return invalid
    found = client(vault).note_exists(args.name)
    if not found:
        return fail(
            args,
            3,
            "missing",
            nxt="agentself note list",
            extra={"name": args.name, "exists": False},
        )
    return CliSuccess({"name": args.name, "exists": True}, redact=False)

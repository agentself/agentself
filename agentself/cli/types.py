"""Types shared by the dynamic CLI parser and command dispatcher."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from agentself.cli.outcomes import CliOutcome


class CommandArguments(Protocol):
    """The parser fields consumed by the dispatcher for every command."""

    command: str | None
    as_raw: bool
    identity_dir: str


class Handler(Protocol):
    """A lazily loaded command handler."""

    def __call__(self, args: CommandArguments, vault: Path) -> CliOutcome: ...


class EmailCommandArguments(CommandArguments, Protocol):
    """Arguments shared by the email connect, message, and filter commands."""

    do_continue: bool
    setup_state: str
    result_file: str
    to: str
    subject: str
    body: str | None
    from_file: str
    message_id: str | None
    body_file: str
    status: Literal["new", "seen"] | None
    acted_filter: bool | str | None
    rejected_filter: bool | None
    limit: int | None
    query: str
    mark_state: Literal["acted", "unacted", "rejected"]

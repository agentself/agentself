from __future__ import annotations

import secrets
from typing import Any

from agentself.internal.names import (
    INTERNAL_PREFIX,
    NOTE_PREFIX,
    SETUP_PREFIX,
    require_safe_token,
)

SETUP_CONNECTED = "connected"
SETUP_INPUT_REQUIRED = "input_required"
SETUP_ACTION_REQUIRED = "action_required"
SETUP_PENDING = "pending"
SETUP_FAILED = "failed"
SETUP_STATUSES = frozenset(
    {
        SETUP_CONNECTED,
        SETUP_INPUT_REQUIRED,
        SETUP_ACTION_REQUIRED,
        SETUP_PENDING,
        SETUP_FAILED,
    }
)

OPTION_ADDRESS = "address"
OPTION_CREDENTIAL = "credential"
OPTION_TYPE_STRING = "string"
OPTION_TYPE_SECRET = "secret"
OPTION_TYPE_CHOICE = "choice"

SETUP_TTL_SECONDS = 3600


def setup_option(
    *,
    name: str,
    type: str,
    required: bool = False,
    sensitive: bool = False,
    default: str | None = None,
    choices: list[str] | tuple[str, ...] | None = None,
    source: str = "",
    help: str = "",
) -> dict[str, Any]:
    """Backend discovery / setup option. Stable public fields only."""

    return {
        "name": name,
        "type": type,
        "required": bool(required),
        "sensitive": bool(sensitive),
        "default": default,
        "choices": list(choices or ()),
        "source": source or "",
        "help": help or "",
    }


def credential_option(
    *,
    required: bool = True,
    source: str = "",
    help: str = "Send credential",
) -> dict[str, Any]:
    return setup_option(
        name=OPTION_CREDENTIAL,
        type=OPTION_TYPE_SECRET,
        required=required,
        sensitive=True,
        source=source,
        help=help,
    )


def address_option(
    *,
    required: bool = False,
    source: str = "",
    help: str = "Mailbox address",
) -> dict[str, Any]:
    return setup_option(
        name=OPTION_ADDRESS,
        type=OPTION_TYPE_STRING,
        required=required,
        sensitive=False,
        source=source,
        help=help,
    )


def new_setup_id() -> str:
    return secrets.token_hex(16)


def setup_hold_name(setup_id: str) -> str:
    return SETUP_PREFIX + require_safe_token(setup_id, "setup id")


def note_hold_name(name: str) -> str:
    return NOTE_PREFIX + require_safe_token(name, "name")


def note_public_name(hold: str) -> str:
    if hold.startswith(NOTE_PREFIX):
        return hold[len(NOTE_PREFIX) :]
    return hold


def is_internal_name(name: str) -> bool:
    return name.startswith(INTERNAL_PREFIX)


def is_note_name(name: str) -> bool:
    return name.startswith(NOTE_PREFIX)


def is_reserved_secret_name(name: str) -> bool:
    return is_internal_name(name) or is_note_name(name)


def continue_command(setup_id: str) -> str:
    return f"agentself email connect --continue {setup_id}"


def setup_status_of(payload: object) -> str:
    if not isinstance(payload, dict):
        return SETUP_FAILED
    status = str(payload.get("status") or "").strip()
    if status in SETUP_STATUSES:
        return status
    if payload.get("owned_address") and str(payload.get("address") or "").strip():
        return SETUP_CONNECTED
    return SETUP_INPUT_REQUIRED

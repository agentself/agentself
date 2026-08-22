from __future__ import annotations

import base64
import json
from typing import Any

from agentself.internal.names import INTERNAL_PREFIX

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

SOURCE_AGENTMAIL_CREDENTIAL = "AGENTSELF_AGENTMAIL_API_KEY"
SOURCE_IMAP_CREDENTIAL = "AGENTSELF_MAIL_PASSWORD"

HELP_AGENTMAIL_CREDENTIAL = (
    "AgentMail API key (starts with am_). Create it at https://console.agentmail.to "
    "under API Keys. Signup or confirm mail may contain the key or a link — read that "
    "inbox if you have it, else ask the operator. Env AGENTSELF_AGENTMAIL_API_KEY. "
    "Write the key to a file and continue with --result-file. Cannot obtain a key: "
    "agentself init --force --email imap, or stop."
)
HELP_AGENTMAIL_ADDRESS = (
    "Inbox address when this key owns more than one. Use an address the provider "
    "listed. Do not invent one."
)
HELP_IMAP_ADDRESS = "Existing mailbox address (user@domain). Do not invent an address."
HELP_IMAP_CREDENTIAL = (
    "Password or app password for that mailbox. Gmail and Outlook need an app "
    "password, not the login password. Env AGENTSELF_MAIL_PASSWORD. Write it to a "
    "file and continue with --result-file."
)
HELP_IMAP_MAIL_HOST = "Shared mail host when IMAP and SMTP use the same hostname."
HELP_IMAP_IMAP_HOST = "IMAP host override. Default is imap.<address-domain>."
HELP_IMAP_SMTP_HOST = "SMTP host override. Default is smtp.<address-domain>."


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
    help: str = "Secret required to connect. Write it to --result-file and continue.",
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


def is_internal_name(name: str) -> bool:
    return name.startswith(INTERNAL_PREFIX)


def is_reserved_secret_name(name: str) -> bool:
    return is_internal_name(name)


def encode_state(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_state(state: str) -> dict[str, object] | None:
    text = (state or "").strip()
    if not text:
        return None
    pad = "=" * ((4 - len(text) % 4) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(text + pad))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {str(key): value for key, value in data.items()}


def continue_command(state: str) -> str:
    return f"agentself email connect --continue --state {state}"


def setup_status_of(payload: object) -> str:
    if not isinstance(payload, dict):
        return SETUP_FAILED
    status = str(payload.get("status") or "").strip()
    if status in SETUP_STATUSES:
        return status
    if payload.get("owned_address") and str(payload.get("address") or "").strip():
        return SETUP_CONNECTED
    return SETUP_INPUT_REQUIRED

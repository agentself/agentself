from __future__ import annotations

import base64
import json
from typing import Any, Literal, NotRequired, TypedDict

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


class SetupAction(TypedDict):
    """Backend-provided human assistance action."""

    kind: Literal["open_url"]
    label: str
    url: str


class SetupOption(TypedDict):
    """Backend-neutral input requested by the shared setup renderer."""

    name: str
    type: Literal["string", "secret", "choice"]
    required: bool
    sensitive: bool
    prompt: str
    help: str
    source: str | None
    choices: list[str]
    action: NotRequired[SetupAction | None]


SOURCE_AGENTMAIL_CREDENTIAL = "AGENTSELF_AGENTMAIL_API_KEY"
SOURCE_IMAP_CREDENTIAL = "AGENTSELF_MAIL_PASSWORD"

HELP_AGENTMAIL_CREDENTIAL = (
    "AgentMail API key (starts with am_). Use an existing approved key. If none is "
    "available, open API Keys at https://console.agentmail.to and wait for the operator "
    "to create and copy one. Creating a new AgentMail "
    "organization through signup is an external account action and requires explicit "
    "user authorization. When that exact action is authorized for a first-time, "
    "unclaimed signup, follow https://docs.agentmail.to/api-reference/agent/sign-up "
    "once with the approved email identity and capture the key from its HTTP response. "
    "If signup reports that the identity is claimed, forbidden, or unavailable, stop "
    "and ask the user; do not probe aliases or disposable email providers. The OTP or "
    "confirmation email does not contain the key. A key is shown once and cannot be "
    "retrieved; if lost, create another in the console. Stop credential discovery as "
    "soon as one key validates. For transient use, provide "
    "AGENTSELF_EMAIL_CREDENTIAL or AGENTSELF_AGENTMAIL_API_KEY on every email "
    "invocation. To store it in this identity, write the key to a file and continue "
    "with --result-file. Cannot obtain a key: agentself init --force --email imap, "
    "or stop."
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
    prompt: str = "",
    action: SetupAction | None = None,
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
        "prompt": prompt or name,
        "action": action,
    }


def credential_option(
    *,
    required: bool = True,
    source: str = "",
    help: str = "Secret required to connect. Write it to --result-file and continue.",
    prompt: str = "Paste the credential",
    action: SetupAction | None = None,
) -> dict[str, Any]:
    return setup_option(
        name=OPTION_CREDENTIAL,
        type=OPTION_TYPE_SECRET,
        required=required,
        sensitive=True,
        source=source,
        help=help,
        prompt=prompt,
        action=action,
    )


def address_option(
    *,
    required: bool = False,
    source: str = "",
    help: str = "Mailbox address",
    prompt: str = "Mailbox address",
    choices: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    return setup_option(
        name=OPTION_ADDRESS,
        type=OPTION_TYPE_CHOICE if choices else OPTION_TYPE_STRING,
        required=required,
        sensitive=False,
        source=source,
        help=help,
        prompt=prompt,
        choices=choices,
    )


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
    return (
        f"agentself --json email connect --continue --state {state} --result-file PATH"
    )


def setup_status_of(payload: object) -> str:
    if not isinstance(payload, dict):
        return SETUP_FAILED
    status = str(payload.get("status") or "").strip()
    if status in SETUP_STATUSES:
        return status
    if payload.get("owned_address") and str(payload.get("address") or "").strip():
        return SETUP_CONNECTED
    return SETUP_INPUT_REQUIRED

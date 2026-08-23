from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
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
    kind: Literal["open_url"]
    label: str
    url: str


class SetupOption(TypedDict):
    name: str
    type: Literal["string", "secret", "choice"]
    required: bool
    sensitive: bool
    prompt: str
    help: str
    source: str | None
    choices: list[str]
    action: NotRequired[SetupAction | None]
    persist: NotRequired[bool]
    persist_as: NotRequired[str]
    runtime_only: NotRequired[bool]


ENV_EMAIL_ADDRESS = "AGENTSELF_EMAIL_ADDRESS"
ENV_EMAIL_CREDENTIAL = "AGENTSELF_EMAIL_CREDENTIAL"

_PUBLIC_OPTION_KEYS = (
    "name",
    "type",
    "required",
    "sensitive",
    "default",
    "choices",
    "source",
    "help",
    "prompt",
    "action",
)


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
    persist: bool = False,
    persist_as: str = "",
    runtime_only: bool = False,
) -> dict[str, Any]:
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
        "persist": bool(persist),
        "persist_as": persist_as or "",
        "runtime_only": bool(runtime_only),
    }


def public_setup_option(option: dict[str, object]) -> dict[str, object]:
    return {key: option[key] for key in _PUBLIC_OPTION_KEYS if key in option}


def option_named(
    options: Sequence[Mapping[str, Any]],
    name: str,
    **updates: object,
) -> dict[str, Any]:
    for item in options:
        if item.get("name") == name:
            return {**item, **updates}
    raise KeyError(name)


def credential_option(
    *,
    required: bool = True,
    source: str = "",
    help: str = "Secret required to connect. Write it to --result-file and continue.",
    prompt: str = "Paste the credential",
    action: SetupAction | None = None,
    persist: bool = False,
    persist_as: str = "",
    runtime_only: bool = False,
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
        persist=persist,
        persist_as=persist_as,
        runtime_only=runtime_only,
    )


def address_option(
    *,
    required: bool = False,
    source: str = "",
    help: str = "Mailbox address",
    prompt: str = "Mailbox address",
    choices: list[str] | tuple[str, ...] | None = None,
    persist: bool = False,
    persist_as: str = "",
    runtime_only: bool = False,
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
        persist=persist,
        persist_as=persist_as,
        runtime_only=runtime_only,
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
    return data if isinstance(data, dict) else None


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

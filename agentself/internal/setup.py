from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, NotRequired, TypedDict, cast

SetupStatus = Literal[
    "connected", "input_required", "action_required", "pending", "failed"
]
SetupOptionType = Literal["string", "secret", "choice"]

SETUP_CONNECTED: Literal["connected"] = "connected"
SETUP_INPUT_REQUIRED: Literal["input_required"] = "input_required"
SETUP_ACTION_REQUIRED: Literal["action_required"] = "action_required"
SETUP_PENDING: Literal["pending"] = "pending"
SETUP_FAILED: Literal["failed"] = "failed"
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
PRIVATE_SETUP_OUTPUTS = "private_outputs"
OPTION_TYPE_STRING: Literal["string"] = "string"
OPTION_TYPE_SECRET: Literal["secret"] = "secret"
OPTION_TYPE_CHOICE: Literal["choice"] = "choice"


class SetupAction(TypedDict):
    kind: Literal["open_url"]
    label: str
    url: str


class SetupOption(TypedDict):
    name: str
    type: SetupOptionType
    required: bool
    sensitive: bool
    help: str
    source: str | None
    choices: list[str]
    default: NotRequired[str | None]
    action: NotRequired[SetupAction | None]
    persist: NotRequired[bool]
    persist_as: NotRequired[str]
    runtime_only: NotRequired[bool]


class SetupResult(TypedDict, total=False):
    """Backend setup result.

    ``private_outputs`` is an in-process handoff to the custody manager. The
    manager may persist declared setup options from it, but public setup views
    must never include it.
    """

    status: SetupStatus
    address: str | None
    owned_address: bool
    needs_domain: bool
    option: SetupOption
    continuation: object
    private_outputs: Mapping[str, str]
    reason: str
    message: str
    retryable: bool
    human_action_required: bool


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
    "action",
)


def setup_option(
    *,
    name: str,
    type: SetupOptionType,
    required: bool = False,
    sensitive: bool = False,
    default: str | None = None,
    choices: list[str] | tuple[str, ...] | None = None,
    source: str = "",
    help: str = "",
    action: SetupAction | None = None,
    persist: bool = False,
    persist_as: str = "",
    runtime_only: bool = False,
) -> SetupOption:
    return cast(
        SetupOption,
        {
            "name": name,
            "type": type,
            "required": bool(required),
            "sensitive": bool(sensitive),
            "default": default,
            "choices": list(choices or ()),
            "source": source or "",
            "help": help or "",
            "action": action,
            "persist": bool(persist),
            "persist_as": persist_as or "",
            "runtime_only": bool(runtime_only),
        },
    )


def public_setup_option(option: Mapping[str, object]) -> dict[str, object]:
    return {key: option[key] for key in _PUBLIC_OPTION_KEYS if key in option}


def option_named(
    options: Sequence[Mapping[str, Any]],
    name: str,
    **updates: object,
) -> SetupOption:
    for item in options:
        if item.get("name") == name:
            return cast(SetupOption, {**item, **updates})
    raise KeyError(name)


def credential_option(
    *,
    required: bool = True,
    source: str = "",
    help: str = "Secret required to connect. Write it to --result-file and continue.",
    action: SetupAction | None = None,
    persist: bool = False,
    persist_as: str = "",
    runtime_only: bool = False,
) -> SetupOption:
    return setup_option(
        name=OPTION_CREDENTIAL,
        type=OPTION_TYPE_SECRET,
        required=required,
        sensitive=True,
        source=source,
        help=help,
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
    choices: list[str] | tuple[str, ...] | None = None,
    persist: bool = False,
    persist_as: str = "",
    runtime_only: bool = False,
) -> SetupOption:
    return setup_option(
        name=OPTION_ADDRESS,
        type=OPTION_TYPE_CHOICE if choices else OPTION_TYPE_STRING,
        required=required,
        sensitive=False,
        source=source,
        help=help,
        choices=choices,
        persist=persist,
        persist_as=persist_as,
        runtime_only=runtime_only,
    )


def encode_state(payload: Mapping[str, object]) -> str:
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
    return f"agentself email connect --continue --state {state} --result-file PATH"


def setup_status_of(payload: object) -> SetupStatus:
    if not isinstance(payload, dict):
        return SETUP_FAILED
    status = str(payload.get("status") or "").strip()
    if status in SETUP_STATUSES:
        return cast(SetupStatus, status)
    if payload.get("owned_address") and str(payload.get("address") or "").strip():
        return SETUP_CONNECTED
    return SETUP_INPUT_REQUIRED


def human_action_required_of(payload: object, status: SetupStatus | None = None) -> bool:
    """Derive human_action_required from status unless the backend value matches."""

    resolved = status if status is not None else setup_status_of(payload)
    derived = resolved == SETUP_ACTION_REQUIRED
    if not isinstance(payload, dict) or "human_action_required" not in payload:
        return derived
    supplied = bool(payload.get("human_action_required"))
    return supplied if supplied == derived else derived

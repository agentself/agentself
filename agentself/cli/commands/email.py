from __future__ import annotations

from pathlib import Path

from agentself.cli.io import load_value_file, store_value_file, value_meta
from agentself.cli.outcomes import CliOutcome, CliRaw, CliSuccess
from agentself.cli.runtime import client, fail
from agentself.internal.custody.errors import ChannelFailure
from agentself.internal.setup import (
    SETUP_ACTION_REQUIRED,
    SETUP_CONNECTED,
    SETUP_FAILED,
    SETUP_PENDING,
    continue_command,
    setup_status_of,
)


def connect_email(args, vault: Path) -> CliOutcome:
    answers, err = _connect_answers(args)
    if err is not None:
        return err
    try:
        result = client(vault).email_connect(
            answers=answers or None,
            state=(args.setup_state or "").strip() or None,
        )
    except ChannelFailure as exc:
        return _email_connect_channel_fail(args, exc)
    return _email_connect_result(args, result)


def show_email(args, vault: Path) -> CliOutcome:
    email = client(vault).identity().get("email")
    email = email if isinstance(email, dict) else {}
    ready = bool(email.get("owned_address") and email.get("address"))
    return CliSuccess({**email, "ready": ready})


def send_email(args, vault: Path) -> CliOutcome:
    client(vault).email_send(args.to, args.subject, args.body)
    return CliSuccess({"to": args.to, "subject": args.subject})


def mark_email(args, vault: Path) -> CliOutcome:
    acted = args.mark_state == "acted"
    client(vault).email_mark(args.message_id, acted=acted)
    return CliSuccess({"id": args.message_id, "acted": acted})


def receive_email(args, vault: Path) -> CliOutcome:
    ref = (args.message_id or "").strip()
    path = (args.body_file or "").strip()
    as_raw = bool(getattr(args, "as_raw", False))
    if path and not ref:
        return fail(
            args,
            2,
            "refused",
            "--file requires a message ref or ID",
            nxt="agentself email receive REF --file PATH",
        )
    if as_raw and not ref:
        return fail(
            args,
            2,
            "refused",
            "--raw requires a message ref or ID",
            nxt="agentself email receive REF --raw",
        )
    messages = client(vault).email_receive(
        message_id=args.message_id,
        include_body=bool(path or as_raw),
    )
    if as_raw:
        body = str(messages[0].get("body", "")) if messages else ""
        return CliRaw(body)
    file_error = _prepare_received_messages(args, messages, path)
    if file_error is not None:
        return file_error
    return CliSuccess({"messages": messages})


def list_email(args, vault: Path) -> CliOutcome:
    messages = client(vault).email_list(status=args.status, acted=args.acted_filter)
    return CliSuccess({"messages": messages})


def find_email(args, vault: Path) -> CliOutcome:
    messages = client(vault).email_find(
        args.query, status=args.status, acted=args.acted_filter
    )
    return CliSuccess({"messages": messages})


def _prepare_received_messages(
    args, messages: list[dict[str, object]], path: str
) -> CliOutcome | None:
    if path and messages:
        body = str(messages[0].get("body", ""))
        try:
            store_value_file(path, body)
        except OSError:
            return fail(args, 1, "error", "file")
        messages[0]["body_file"] = path
        messages[0]["body_bytes"] = str(value_meta(body)["bytes"])
        messages[0]["body_sha256"] = str(value_meta(body)["sha256"])
    for message in messages:
        message.pop("body", None)
    return None


def _connect_answers(args) -> tuple[dict[str, str], CliOutcome | None]:
    do_continue = args.do_continue
    state = (args.setup_state or "").strip()
    path = (args.result_file or "").strip()
    if not do_continue:
        if path:
            return {}, fail(
                args,
                2,
                "refused",
                "--result-file needs --continue",
                nxt="agentself email connect --help",
            )
        if state:
            return {}, fail(
                args,
                2,
                "refused",
                "--state needs --continue",
                nxt="agentself email connect --help",
            )
        return {}, None
    if not state:
        return {}, fail(
            args,
            2,
            "refused",
            "--continue needs --state",
            nxt="agentself email connect --help",
        )
    if path:
        try:
            text = load_value_file(path)
        except OSError:
            return {}, fail(
                args,
                1,
                "error",
                "file",
                nxt="agentself email connect --help",
            )
        return {"value": text} if text else {}, None
    return {}, None


def _compact_setup_option(option: object) -> dict[str, object] | None:
    if not isinstance(option, dict):
        return None
    name = str(option.get("name") or "").strip()
    if not name:
        return None
    payload: dict[str, object] = {"name": name}
    option_type = str(option.get("type") or "").strip()
    if option_type:
        payload["type"] = option_type
    if option.get("sensitive"):
        payload["sensitive"] = True
    choices = [
        str(choice).strip()
        for choice in (option.get("choices") or [])
        if str(choice).strip()
    ]
    if choices:
        payload["choices"] = choices
    action = option.get("action")
    if isinstance(action, dict):
        payload["action"] = action
    return payload


def _setup_public(result: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {
        key: result[key]
        for key in (
            "status",
            "state",
            "human_action_required",
            "continue",
            "message",
        )
        if key in result
    }
    compact = _compact_setup_option(result.get("option"))
    if compact is not None:
        payload["option"] = compact
    if "continue" not in payload and result.get("state"):
        payload["continue"] = continue_command(str(result["state"]))
    if "human_action_required" not in payload:
        payload["human_action_required"] = (
            setup_status_of(result) == SETUP_ACTION_REQUIRED
        )
    return payload


def _email_setup_pending(args, result: dict[str, object], status: str) -> CliOutcome:
    token = str(result.get("state") or "")
    nxt = str(result.get("continue") or "") or (
        continue_command(token) if token else "agentself email connect --help"
    )
    extra = _setup_public(result)
    reason = {
        SETUP_ACTION_REQUIRED: "human action required",
        SETUP_PENDING: "pending",
    }.get(status, "input required")
    return fail(args, 3, "missing", reason, nxt=nxt, extra=extra)


def _email_connect_ok(args, address: str | None) -> CliOutcome:
    addr = (address or "").strip() or None
    return CliSuccess({"address": addr, "status": SETUP_CONNECTED})


def _email_connect_result(args, result: dict[str, object]) -> CliOutcome:
    status = setup_status_of(result)
    if status == SETUP_CONNECTED:
        addr = str(result.get("address") or "").strip()
        if not addr:
            return fail(
                args,
                1,
                "error",
                "no inbox",
                nxt="agentself backends email",
            )
        return _email_connect_ok(args, addr)
    if status == SETUP_FAILED:
        reason = str(result.get("reason") or "error")
        return fail(
            args,
            1,
            "error",
            reason,
            nxt="agentself backends email",
            extra=_setup_public(result),
        )
    return _email_setup_pending(args, result, status)


def _email_connect_channel_fail(args, exc: ChannelFailure) -> CliOutcome:
    reason = exc.reason
    if reason == "no_token":
        return fail(
            args,
            3,
            "missing",
            "need email.credential",
            nxt="agentself email connect",
        )
    if reason == "need_address":
        return fail(
            args,
            3,
            "missing",
            "need email.address",
            nxt="agentself email connect",
        )
    if reason == "invalid_credential":
        return fail(
            args,
            1,
            "error",
            "invalid credentials",
            nxt="agentself email connect",
        )
    return fail(
        args,
        1,
        "error",
        reason,
        nxt="agentself backends email",
    )

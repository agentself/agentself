from __future__ import annotations

from pathlib import Path

from agentself.cli.io import load_value_file, store_value_file, value_meta
from agentself.cli.outcomes import CliOutcome, CliRaw, CliSuccess
from agentself.cli.runtime import (
    client,
    fail,
    value_from_file_or_arg,
    value_source_error,
)
from agentself.cli.types import EmailCommandArguments
from agentself.internal.custody.errors import ChannelFailure
from agentself.internal.mail_state import MAIL_LIST_CAP
from agentself.internal.setup import (
    SETUP_ACTION_REQUIRED,
    SETUP_CONNECTED,
    SETUP_FAILED,
    SETUP_PENDING,
    continue_command,
    human_action_required_of,
    setup_status_of,
)

_SIGNUP_FAILURES = frozenset(
    {
        "setup_conflict",
        "setup_forbidden",
        "setup_rejected",
        "backend_unavailable",
    }
)


def connect_email(args: EmailCommandArguments, vault: Path) -> CliOutcome:
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


def show_email(args: EmailCommandArguments, vault: Path) -> CliOutcome:
    email = client(vault).identity().get("email")
    email = email if isinstance(email, dict) else {}
    ready = bool(email.get("owned_address") and email.get("address"))
    return CliSuccess({**email, "ready": ready})


def send_email(args: EmailCommandArguments, vault: Path) -> CliOutcome:
    body, err = value_from_file_or_arg(
        args.body,
        getattr(args, "from_file", "") or "",
        both_error="body and --file",
        strip_newline=False,
        empty_is_missing=False,
    )
    if err is not None or body is None:
        return value_source_error(
            args,
            err or "need a value",
            "agentself email send --help",
        )
    sent = client(vault).email_send(args.to, args.subject, body)
    payload: dict[str, object] = {"to": args.to, "subject": args.subject}
    if sent.get("id"):
        payload["id"] = sent["id"]
    if sent.get("ref"):
        payload["ref"] = sent["ref"]
    return CliSuccess(payload)


def mark_email(args: EmailCommandArguments, vault: Path) -> CliOutcome:
    rejected = args.mark_state == "rejected"
    acted = args.mark_state == "acted"
    client(vault).email_mark(args.message_id, acted=acted, rejected=rejected)
    return CliSuccess(
        {
            "id": args.message_id,
            "acted": acted,
            "rejected": rejected,
        }
    )


def receive_email(args: EmailCommandArguments, vault: Path) -> CliOutcome:
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
    if not ref:
        messages, trunc_err = _list_headers(args, vault, status="new")
        if trunc_err is not None:
            return trunc_err
    else:
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
    payload: dict[str, object] = {"messages": messages}
    if not ref and getattr(args, "_truncated", False):
        payload["truncated"] = True
    return CliSuccess(payload)


def list_email(args: EmailCommandArguments, vault: Path) -> CliOutcome:
    messages, err = _list_headers(args, vault, status=args.status)
    if err is not None:
        return err
    payload: dict[str, object] = {"messages": messages}
    if getattr(args, "_truncated", False):
        payload["truncated"] = True
    return CliSuccess(payload)


def find_email(args: EmailCommandArguments, vault: Path) -> CliOutcome:
    acted, rejected = _task_filters(args)
    messages = client(vault).email_find(
        args.query,
        status=args.status,
        acted=acted,
        rejected=rejected,
    )
    return CliSuccess({"messages": messages})


def _task_filters(args: EmailCommandArguments) -> tuple[bool | None, bool | None]:
    raw = getattr(args, "acted_filter", None)
    if raw == "rejected":
        return None, True
    if raw is True:
        return True, None
    if raw is False:
        return False, False
    return None, None


def _parse_limit(args: EmailCommandArguments) -> tuple[int | None, CliOutcome | None]:
    raw = getattr(args, "limit", None)
    if raw is None:
        return None, None
    if raw < 1 or raw > MAIL_LIST_CAP:
        return None, fail(
            args,
            2,
            "refused",
            "limit must be 1..100",
            nxt="agentself email list --help",
        )
    return raw, None


def _list_headers(
    args: EmailCommandArguments,
    vault: Path,
    *,
    status: str | None,
) -> tuple[list[dict[str, object]], CliOutcome | None]:
    limit, err = _parse_limit(args)
    if err is not None:
        return [], err
    acted, rejected = _task_filters(args)
    wanted = limit if limit is not None else MAIL_LIST_CAP
    fetch = wanted + 1 if wanted < MAIL_LIST_CAP else wanted
    messages = client(vault).email_list(
        status=status, acted=acted, rejected=rejected, limit=fetch
    )
    truncated = len(messages) > wanted or len(messages) >= MAIL_LIST_CAP
    if len(messages) > wanted:
        messages = messages[:wanted]
    setattr(args, "_truncated", truncated)
    return messages, None


def _prepare_received_messages(
    args: EmailCommandArguments, messages: list[dict[str, object]], path: str
) -> CliOutcome | None:
    if path and messages:
        body = str(messages[0].get("body", ""))
        try:
            store_value_file(path, body)
        except OSError:
            return fail(args, 1, "error", "file")
        meta = value_meta(body)
        messages[0]["body_file"] = path
        messages[0]["body_bytes"] = meta["bytes"]
        messages[0]["body_sha256"] = meta["sha256"]
    for message in messages:
        message.pop("body", None)
    return None


def _connect_answers(
    args: EmailCommandArguments,
) -> tuple[dict[str, str], CliOutcome | None]:
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
        except (OSError, UnicodeDecodeError):
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
            "continue",
            "message",
            "retryable",
        )
        if key in result
    }
    compact = _compact_setup_option(result.get("option"))
    if compact is not None:
        payload["option"] = compact
    if "continue" not in payload and result.get("state"):
        payload["continue"] = continue_command(str(result["state"]))
    payload["human_action_required"] = human_action_required_of(result)
    return payload


def _email_setup_pending(
    args: EmailCommandArguments, result: dict[str, object], status: str
) -> CliOutcome:
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


def _email_connect_ok(args: EmailCommandArguments, address: str | None) -> CliOutcome:
    addr = (address or "").strip() or None
    return CliSuccess({"address": addr, "status": SETUP_CONNECTED})


def _email_connect_result(
    args: EmailCommandArguments, result: dict[str, object]
) -> CliOutcome:
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
        nxt = (
            "agentself email connect"
            if reason in _SIGNUP_FAILURES
            else "agentself backends email"
        )
        return fail(
            args,
            1,
            "error",
            reason,
            nxt=nxt,
            extra=_setup_public(result),
        )
    return _email_setup_pending(args, result, status)


def _email_connect_channel_fail(
    args: EmailCommandArguments, exc: ChannelFailure
) -> CliOutcome:
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

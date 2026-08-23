from __future__ import annotations

import builtins
import hashlib
import json
import os
import urllib.error
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from agentself.backends.email.contract import (
    MailboxAccess,
    MailboxError,
    mailbox_view,
    require_addr,
    require_secret,
    secret_or_env,
    setup_needed,
)
from agentself.backends.email.http import request as http_request
from agentself.email_catalog import AGENTMAIL_OPTIONS as OPTIONS
from agentself.email_catalog import SOURCE_AGENTMAIL_CREDENTIAL
from agentself.internal.files import (
    IdentityBusy,
    atomic_write_text,
    ensure_private_dir,
    exclusive,
    identity_home,
)
from agentself.internal.log import Log
from agentself.internal.names import require_safe_token
from agentself.internal.setup import option_named

_API = "https://api.agentmail.to"
_INBOXES_URL = _API + "/v0/inboxes"
_FORBID_LIVE = "AGENTSELF_FORBID_LIVE_AGENTMAIL"
_LIVE_HOST = "api.agentmail.to"

Poster = Callable[[str, dict[str, str], bytes], tuple[int, bytes]]
Getter = Callable[[str, dict[str, str]], tuple[int, bytes]]


class AgentMailMailboxAccess(MailboxAccess):
    """Inbox from address secret or GET /v0/inboxes, never identity@domain."""

    def __init__(
        self,
        vault_root: Path,
        log: Log,
        *,
        domain: str = "",
        poster: Poster | None = None,
        getter: Getter | None = None,
    ) -> None:
        self._root = Path(vault_root)
        self._log = log
        self._domain = (domain or "").strip()
        self._poster = poster
        self._getter = getter

    def _require_credential(
        self,
        identity_id: str,
        credential: str | None,
        event: str,
        extra: str | None = None,
    ) -> str:
        token = secret_or_env(credential, SOURCE_AGENTMAIL_CREDENTIAL)
        if not token:
            self._log.record(event, identity_id, extra, "error")
            raise MailboxError("missing credentials")
        return require_secret(token)

    def send(
        self,
        identity_id: str,
        to: str,
        subject: str,
        body: str,
        credential: str | None = None,
        address: str | None = None,
    ) -> None:
        require_safe_token(identity_id, "identity id")
        require_addr(to)
        credential = self._require_credential(
            identity_id, credential, "mailbox_send", to
        )
        inbox = self._inbox(identity_id, credential, address)
        payload = json.dumps({"to": to, "subject": subject, "text": body}).encode(
            "utf-8"
        )
        self._request(_send_url(inbox["inbox_id"]), credential, payload)
        self._log.record("mailbox_send", identity_id, to, "ok")

    def receive(
        self,
        identity_id: str,
        *,
        credential: str | None = None,
        address: str | None = None,
        message_id: str | None = None,
    ) -> builtins.list[dict[str, str]]:
        require_safe_token(identity_id, "identity id")
        credential = self._require_credential(identity_id, credential, "mailbox_recv")
        try:
            with exclusive(self._root):
                return self._recv_locked(identity_id, credential, address, message_id)
        except IdentityBusy as exc:
            raise MailboxError("rpc failed") from exc

    def _recv_locked(
        self,
        identity_id: str,
        credential: str,
        address: str | None,
        message_id: str | None,
    ) -> builtins.list[dict[str, str]]:
        inbox = self._inbox(identity_id, credential, address)
        inbox_id = str(inbox.get("inbox_id") or "")
        listed = self._list_messages(inbox_id, credential, "recv failed")
        seen = self._seen_dir(identity_id)
        ensure_private_dir(seen)
        wanted = (message_id or "").strip()
        messages: list[dict[str, str]] = []
        for item in listed:
            mid = str(item.get("message_id") or "")
            if not mid:
                continue
            if wanted and mid != wanted:
                continue
            mark = seen / _safe_filename(mid)
            if not wanted and mark.is_file():
                continue
            parsed = _meta(item)
            fetched = self._get_message(_message_url(inbox_id, mid), credential)
            if fetched is None:
                parsed["body"] = str(item.get("preview") or "")
                parsed["reason"] = "mailbox_error"
            else:
                parsed["body"] = _body_of(fetched)
                atomic_write_text(mark, mid)
            messages.append(parsed)
            if wanted:
                break
        self._log.record("mailbox_recv", identity_id, None, "ok")
        return messages

    def list(
        self,
        identity_id: str,
        *,
        credential: str | None = None,
        address: str | None = None,
    ) -> builtins.list[dict[str, str]]:
        require_safe_token(identity_id, "identity id")
        credential = self._require_credential(identity_id, credential, "mailbox_list")
        inbox = self._inbox(identity_id, credential, address)
        inbox_id = str(inbox.get("inbox_id") or "")
        listed = self._list_messages(inbox_id, credential, "list failed")
        items = [_meta(item) for item in listed]
        self._write_inbox_id(identity_id, inbox_id)
        self._log.record("mailbox_list", identity_id, None, "ok")
        return items

    def describe(
        self,
        identity_id: str,
        *,
        credential: str | None = None,
        address: str | None = None,
    ) -> dict[str, object]:
        require_safe_token(identity_id, "identity id")
        wanted = (address or "").strip()
        credential = secret_or_env(credential, SOURCE_AGENTMAIL_CREDENTIAL)
        if not credential:
            return mailbox_view()
        credential = require_secret(credential)
        inbox = self._inbox(identity_id, credential, wanted or None)
        email = str(inbox.get("email") or "").strip()
        if not email:
            raise MailboxError("no inbox")
        return mailbox_view(email, owned_address=True)

    def setup_options(self) -> tuple[dict[str, object], ...]:
        return OPTIONS

    def connect(
        self,
        identity_id: str,
        *,
        credential: str | None = None,
        address: str | None = None,
        answers: dict[str, str] | None = None,
        state: object | None = None,
    ) -> dict[str, object]:
        require_safe_token(identity_id, "identity id")
        del state
        extra = answers or {}
        wanted = (address or extra.get("address") or "").strip()
        token = secret_or_env(
            credential or extra.get("credential"), SOURCE_AGENTMAIL_CREDENTIAL
        )
        if not token:
            self._log.record("mailbox_connect", identity_id, None, "error")
            return setup_needed(
                option_named(OPTIONS, "credential"),
                human_action_required=True,
            )
        credential = require_secret(token)
        live = _live_inboxes(self._listed_inboxes(credential))
        if wanted:
            target = wanted.lower()
            inbox = next(
                (
                    item
                    for item in live
                    if str(item.get("email") or "").strip().lower() == target
                ),
                None,
            )
            if inbox is None:
                self._log.record("mailbox_connect", identity_id, None, "error")
                raise MailboxError("no inbox")
        elif len(live) > 1:
            self._log.record("mailbox_connect", identity_id, None, "error")
            return setup_needed(
                option_named(
                    OPTIONS,
                    "address",
                    required=True,
                    prompt="Choose the inbox for this identity",
                    type="choice",
                    choices=[
                        str(item.get("email") or "").strip()
                        for item in live
                        if str(item.get("email") or "").strip()
                    ],
                )
            )
        elif len(live) == 1:
            inbox = live[0]
        else:
            inbox = self._create_inbox(identity_id, credential)
        email = str(inbox.get("email") or "").strip()
        inbox_id = inbox.get("inbox_id")
        if not email or not inbox_id:
            self._log.record("mailbox_connect", identity_id, None, "error")
            raise MailboxError("no inbox")
        self._write_inbox_id(identity_id, inbox_id)
        self._log.record("mailbox_connect", identity_id, None, "ok")
        return mailbox_view(email, owned_address=True)

    def _listed_inboxes(self, token: str) -> builtins.list[object]:
        data = _object(self._request(_INBOXES_URL, token), "no inbox")
        inboxes = data.get("inboxes")
        if not isinstance(inboxes, list):
            raise MailboxError("no inbox")
        return inboxes

    def _create_inbox(self, identity_id: str, token: str) -> dict[str, object]:
        payload = json.dumps({"client_id": "agentself-" + identity_id}).encode("utf-8")
        created = _object(self._request(_INBOXES_URL, token, payload), "no inbox")
        email = str(created.get("email") or "").strip()
        if not created.get("inbox_id") or not email:
            raise MailboxError("no inbox")
        return created

    def _inbox(
        self, identity_id: str, token: str, address: str | None
    ) -> dict[str, object]:
        inboxes = self._listed_inboxes(token)
        wanted = (address or "").strip()
        if wanted:
            target = wanted.lower()
            for item in inboxes:
                if not isinstance(item, dict):
                    continue
                email = str(item.get("email") or "").strip()
                if email.lower() == target and item.get("inbox_id"):
                    return item
            raise MailboxError("no inbox")
        if (
            len(inboxes) == 1
            and isinstance(inboxes[0], dict)
            and inboxes[0].get("inbox_id")
        ):
            return inboxes[0]
        raise MailboxError("no inbox")

    def _list_messages(
        self, inbox_id: str, token: str, fail: str
    ) -> builtins.list[dict[str, object]]:
        data = _object(self._request(_messages_url(inbox_id), token), fail)
        messages = data.get("messages")
        if messages is None:
            return []
        if not isinstance(messages, list):
            raise MailboxError(fail)
        return [item for item in messages if isinstance(item, dict)]

    def _request(self, url: str, token: str, payload: bytes | None = None) -> bytes:
        token = require_secret(token)
        headers = {"Authorization": "Bearer " + token}
        try:
            if payload is None:
                status, body = (self._getter or _default_getter)(url, headers)
            else:
                headers["Content-Type"] = "application/json"
                status, body = (self._poster or _default_poster)(url, headers, payload)
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise MailboxError("rpc failed") from exc
        if not 200 <= status < 300:
            raise MailboxError(_http_error(status))
        return body

    def _get_message(self, url: str, token: str) -> dict[str, object] | None:
        try:
            data = _json(self._request(url, token))
        except MailboxError:
            return None
        return data if isinstance(data, dict) else None

    def _write_inbox_id(self, identity_id: str, inbox_id: object) -> None:
        folder = ensure_private_dir(self._agentmail_dir(identity_id))
        path = folder / "inbox_id"
        atomic_write_text(path, str(inbox_id))

    def _agentmail_dir(self, identity_id: str) -> Path:
        return identity_home(self._root, identity_id) / "agentmail"

    def _seen_dir(self, identity_id: str) -> Path:
        return self._agentmail_dir(identity_id) / "seen"


def _http_error(status: int) -> str:
    if status in (401, 403):
        return "invalid credentials"
    return "rpc failed"


def _live_inboxes(inboxes: list[object]) -> list[dict[str, object]]:
    return [
        item
        for item in inboxes
        if isinstance(item, dict)
        and item.get("inbox_id")
        and str(item.get("email") or "").strip()
    ]


def _enc(value: object) -> str:
    return quote(str(value), safe="")


def _send_url(inbox_id: object) -> str:
    return f"{_API}/v0/inboxes/{_enc(inbox_id)}/messages/send"


def _messages_url(inbox_id: object) -> str:
    return f"{_API}/v0/inboxes/{_enc(inbox_id)}/messages"


def _message_url(inbox_id: object, message_id: str) -> str:
    return f"{_API}/v0/inboxes/{_enc(inbox_id)}/messages/{_enc(message_id)}"


def _safe_filename(message_id: str) -> str:
    try:
        return require_safe_token(message_id, "id")
    except ValueError:
        digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()
        return "m" + digest[:32]


def _json(body: bytes) -> object | None:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _object(body: bytes, fail: str) -> dict[str, object]:
    data = _json(body)
    if not isinstance(data, dict):
        raise MailboxError(fail)
    return data


def _as_text(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _meta(item: dict[str, object]) -> dict[str, str]:
    return {
        "id": str(item.get("message_id") or ""),
        "from": _as_text(item.get("from")),
        "to": _as_text(item.get("to")),
        "subject": _as_text(item.get("subject")),
    }


def _body_of(payload: dict[str, object]) -> str:
    for key in ("text", "extracted_text", "preview"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    return ""


def _forbid_hosts() -> tuple[str, ...]:
    raw = os.environ.get(_FORBID_LIVE, "").strip().lower()
    if raw in ("", "0", "false", "no", "off"):
        return ()
    return (_LIVE_HOST,)


def _default_poster(
    url: str, headers: dict[str, str], payload: bytes
) -> tuple[int, bytes]:
    return http_request(
        url, headers, payload, method="POST", forbid_hosts=_forbid_hosts()
    )


def _default_getter(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
    return http_request(url, headers, method="GET", forbid_hosts=_forbid_hosts())

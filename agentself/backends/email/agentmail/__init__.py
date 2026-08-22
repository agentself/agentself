from __future__ import annotations

import builtins
import hashlib
import json
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
    setup_needed,
)
from agentself.backends.email.http import request as http_request
from agentself.internal.files import (
    VaultBusy,
    atomic_write_text,
    ensure_private_dir,
    exclusive,
    identity_home,
)
from agentself.internal.log import Log
from agentself.internal.names import require_safe_token
from agentself.internal.setup import address_option, credential_option

_API = "https://api.agentmail.to"

Poster = Callable[[str, dict[str, str], bytes], tuple[int, bytes]]
Getter = Callable[[str, dict[str, str]], tuple[int, bytes]]


class AgentMailMailboxAccess(MailboxAccess):
    """Inbox from address hold or GET /v0/inboxes, never principal@domain."""

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

    def send(
        self,
        principal_id: str,
        to: str,
        subject: str,
        body: str,
        send_token: str | None = None,
        address: str | None = None,
    ) -> None:
        require_safe_token(principal_id, "principal id")
        require_addr(to)
        if not send_token:
            self._log.record("mailbox_send", principal_id, to, "error")
            raise MailboxError("send failed")
        send_token = require_secret(send_token)
        inbox = self._inbox(principal_id, send_token, address)
        url = _send_url(inbox["inbox_id"])
        payload = json.dumps({"to": to, "subject": subject, "text": body}).encode(
            "utf-8"
        )
        self._post(url, send_token, payload, "send failed")
        self._log.record("mailbox_send", principal_id, to, "ok")

    def recv(
        self,
        principal_id: str,
        *,
        send_token: str | None = None,
        address: str | None = None,
        message_id: str | None = None,
    ) -> builtins.list[dict[str, str]]:
        require_safe_token(principal_id, "principal id")
        if not send_token:
            self._log.record("mailbox_recv", principal_id, None, "error")
            raise MailboxError("recv failed")
        send_token = require_secret(send_token)
        try:
            with exclusive(self._root):
                return self._recv_locked(principal_id, send_token, address, message_id)
        except VaultBusy as exc:
            raise MailboxError("rpc failed") from exc

    def _recv_locked(
        self,
        principal_id: str,
        send_token: str,
        address: str | None,
        message_id: str | None,
    ) -> builtins.list[dict[str, str]]:
        inbox = self._inbox(principal_id, send_token, address)
        inbox_id = str(inbox.get("inbox_id") or "")
        listed = self._list_messages(inbox_id, send_token, "recv failed")
        seen = self._seen_dir(principal_id)
        ensure_private_dir(seen)
        wanted = (message_id or "").strip()
        messages: list[dict[str, str]] = []
        for item in listed:
            mid = str(item.get("message_id") or "")
            if not mid:
                continue
            if wanted:
                if mid != wanted:
                    continue
            else:
                mark = seen / _safe_filename(mid)
                if mark.is_file():
                    continue
            parsed = _meta(item)
            fetched = self._get_message(_message_url(inbox_id, mid), send_token)
            if fetched is None:
                parsed["body"] = str(item.get("preview") or "")
                parsed["reason"] = "mailbox_error"
            else:
                parsed["body"] = _body_of(fetched)
                mark = seen / _safe_filename(mid)
                atomic_write_text(mark, mid)
            messages.append(parsed)
            if wanted:
                break
        self._log.record("mailbox_recv", principal_id, None, "ok")
        return messages

    def list(
        self,
        principal_id: str,
        *,
        send_token: str | None = None,
        address: str | None = None,
    ) -> builtins.list[dict[str, str]]:
        require_safe_token(principal_id, "principal id")
        if not send_token:
            self._log.record("mailbox_list", principal_id, None, "error")
            raise MailboxError("list failed")
        send_token = require_secret(send_token)
        inbox = self._inbox(principal_id, send_token, address)
        inbox_id = str(inbox.get("inbox_id") or "")
        listed = self._list_messages(inbox_id, send_token, "list failed")
        items = [_meta(item) for item in listed]
        self._write_inbox_id(principal_id, inbox_id)
        self._log.record("mailbox_list", principal_id, None, "ok")
        return items

    def describe(
        self,
        principal_id: str,
        *,
        send_token: str | None = None,
        address: str | None = None,
    ) -> dict[str, object]:
        require_safe_token(principal_id, "principal id")
        wanted = (address or "").strip()
        if wanted:
            return mailbox_view(wanted, owned_address=True)
        if send_token:
            inbox = self._inbox(principal_id, send_token, None)
            email = str(inbox.get("email") or "").strip()
            if not email:
                raise MailboxError("no inbox")
            return mailbox_view(email, owned_address=True)
        return mailbox_view()

    def connect(
        self,
        principal_id: str,
        *,
        send_token: str | None = None,
        address: str | None = None,
        answers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        require_safe_token(principal_id, "principal id")
        extra = answers or {}
        wanted = (address or extra.get("address") or "").strip()
        token = send_token or extra.get("credential") or ""
        if wanted:
            return mailbox_view(wanted, owned_address=True)
        if not token:
            self._log.record("mailbox_connect", principal_id, None, "error")
            return setup_needed([credential_option()])
        send_token = require_secret(token)
        live = _live_inboxes(self._listed_inboxes(send_token))
        if len(live) > 1:
            self._log.record("mailbox_connect", principal_id, None, "error")
            return setup_needed([address_option(required=True)])
        if len(live) == 1:
            inbox = live[0]
        else:
            inbox = self._create_inbox(principal_id, send_token)
        email = str(inbox.get("email") or "").strip()
        inbox_id = inbox.get("inbox_id")
        if not email or not inbox_id:
            self._log.record("mailbox_connect", principal_id, None, "error")
            raise MailboxError("no inbox")
        self._write_inbox_id(principal_id, inbox_id)
        self._log.record("mailbox_connect", principal_id, None, "ok")
        return mailbox_view(email, owned_address=True)

    def _listed_inboxes(self, token: str) -> builtins.list[object]:
        raw = self._get(_inboxes_url(), token, "no inbox")
        data = _object(raw, "no inbox")
        inboxes = data.get("inboxes")
        if not isinstance(inboxes, list):
            raise MailboxError("no inbox")
        return inboxes

    def _create_inbox(self, principal_id: str, token: str) -> dict[str, object]:
        payload = json.dumps({"client_id": "agentself-" + principal_id}).encode("utf-8")
        body = self._post(_inboxes_url(), token, payload, "no inbox")
        created = _object(body, "no inbox")
        email = str(created.get("email") or "").strip()
        if created.get("inbox_id") and email:
            return created
        raise MailboxError("no inbox")

    def _inbox(
        self, principal_id: str, token: str, address: str | None
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
        if len(inboxes) == 1 and isinstance(inboxes[0], dict):
            item = inboxes[0]
            if item.get("inbox_id"):
                return item
        raise MailboxError("no inbox")

    def _list_messages(
        self, inbox_id: str, token: str, fail: str
    ) -> builtins.list[dict[str, object]]:
        raw = self._get(_messages_url(inbox_id), token, fail)
        data = _object(raw, fail)
        messages = data.get("messages")
        if messages is None:
            return []
        if not isinstance(messages, list):
            raise MailboxError(fail)
        return [item for item in messages if isinstance(item, dict)]

    def _get(self, url: str, token: str, fail: str) -> bytes:
        token = require_secret(token)
        headers = {"Authorization": "Bearer " + token}
        getter = self._getter or _default_getter
        try:
            status, body = getter(url, headers)
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise MailboxError("rpc failed") from exc
        if status < 200 or status >= 300:
            raise MailboxError("rpc failed")
        return body

    def _get_message(self, url: str, token: str) -> dict[str, object] | None:
        try:
            token = require_secret(token)
        except MailboxError:
            return None
        headers = {"Authorization": "Bearer " + token}
        getter = self._getter or _default_getter
        try:
            status, body = getter(url, headers)
        except (OSError, urllib.error.URLError, TimeoutError):
            return None
        if status < 200 or status >= 300:
            return None
        data = _json(body)
        if not isinstance(data, dict):
            return None
        return data

    def _post(self, url: str, token: str, payload: bytes, fail: str) -> bytes:
        token = require_secret(token)
        headers = {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        }
        poster = self._poster or _default_poster
        try:
            status, resp = poster(url, headers, payload)
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise MailboxError("rpc failed") from exc
        if status < 200 or status >= 300:
            raise MailboxError("rpc failed")
        return resp

    def _write_inbox_id(self, principal_id: str, inbox_id: object) -> None:
        folder = ensure_private_dir(self._agentmail_dir(principal_id))
        path = folder / "inbox_id"
        atomic_write_text(path, str(inbox_id))

    def _agentmail_dir(self, principal_id: str) -> Path:
        return identity_home(self._root, principal_id) / "agentmail"

    def _seen_dir(self, principal_id: str) -> Path:
        return self._agentmail_dir(principal_id) / "seen"


def _live_inboxes(inboxes: list[object]) -> list[dict[str, object]]:
    live: list[dict[str, object]] = []
    for item in inboxes:
        if not isinstance(item, dict):
            continue
        if item.get("inbox_id") and str(item.get("email") or "").strip():
            live.append(item)
    return live


def _inboxes_url() -> str:
    return _API + "/v0/inboxes"


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
        parts = [str(item) for item in value]
        if len(parts) == 1:
            return parts[0]
        return ", ".join(parts)
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
        if key in payload and payload[key] is not None:
            return str(payload[key])
    return ""


def _default_poster(
    url: str, headers: dict[str, str], payload: bytes
) -> tuple[int, bytes]:
    return http_request(url, headers, payload, method="POST")


def _default_getter(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
    return http_request(url, headers, method="GET")

from __future__ import annotations

import secrets
import time
from pathlib import Path

from agentself.backends.email.contract import (
    MailboxAccess,
    MailboxError,
    mailbox_view,
    require_addr,
)
from agentself.internal.files import (
    IdentityBusy,
    atomic_write_text,
    exclusive,
    identity_home,
)
from agentself.internal.log import Log
from agentself.internal.names import require_safe_token


class MaildirMailboxAccess(MailboxAccess):
    """Test double: local maildir. Not a shipped bind."""

    def __init__(self, vault_root: Path, log: Log, *, domain: str = "") -> None:
        self._root = Path(vault_root)
        self._log = log
        self._domain = (domain or "").strip()

    def send(
        self,
        identity_id: str,
        to: str,
        subject: str,
        body: str,
        credential: str | None = None,
        address: str | None = None,
    ) -> str | None:
        require_safe_token(identity_id, "identity id")
        require_addr(to)
        outbox = self._outbox(identity_id)
        outbox.mkdir(mode=0o700, parents=True, exist_ok=True)
        name = _unique_name()
        path = outbox / name
        atomic_write_text(path, _format_message(identity_id, to, subject, body))
        self._log.record("mailbox_send", identity_id, to, "ok")
        return name

    def receive(
        self,
        identity_id: str,
        *,
        credential: str | None = None,
        address: str | None = None,
        message_id: str | None = None,
        include_body: bool = True,
    ) -> list[dict[str, str]]:
        require_safe_token(identity_id, "identity id")
        try:
            with exclusive(self._root):
                new_dir, cur_dir = self._ensure_maildir(identity_id)
                wanted = (message_id or "").strip()
                if wanted:
                    messages = _take_by_id(new_dir, cur_dir, wanted)
                    self._log.record("mailbox_recv", identity_id, None, "ok")
                    return _with_body(messages, include_body)
                messages = _consume_new(new_dir, cur_dir)
                self._log.record("mailbox_recv", identity_id, None, "ok")
                return _with_body(messages, include_body)
        except IdentityBusy as exc:
            raise MailboxError("rpc failed") from exc

    def list(
        self,
        identity_id: str,
        *,
        credential: str | None = None,
        address: str | None = None,
    ) -> list[dict[str, str]]:
        require_safe_token(identity_id, "identity id")
        new_dir, cur_dir = self._ensure_maildir(identity_id)
        items: list[dict[str, str]] = []
        for folder in (new_dir, cur_dir):
            for path in sorted(p for p in folder.iterdir() if p.is_file()):
                parsed = _parse_message(path)
                items.append(
                    {
                        "id": path.name,
                        "from": parsed.get("from", ""),
                        "to": parsed.get("to", ""),
                        "subject": parsed.get("subject", ""),
                    }
                )
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
        if wanted:
            return mailbox_view(wanted, owned_address=True)
        return mailbox_view(needs_domain=True)

    def _base(self, identity_id: str) -> Path:
        return identity_home(self._root, identity_id)

    def _outbox(self, identity_id: str) -> Path:
        return self._base(identity_id) / "outbox"

    def _ensure_maildir(self, identity_id: str) -> tuple[Path, Path]:
        mail = self._base(identity_id) / "maildir"
        new_dir = mail / "new"
        cur_dir = mail / "cur"
        tmp_dir = mail / "tmp"
        for folder in (new_dir, cur_dir, tmp_dir):
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        return new_dir, cur_dir


def _with_body(
    messages: list[dict[str, str]], include_body: bool
) -> list[dict[str, str]]:
    if include_body:
        return messages
    return [
        {key: value for key, value in item.items() if key != "body"}
        for item in messages
    ]


def _take_by_id(new_dir: Path, cur_dir: Path, wanted: str) -> list[dict[str, str]]:
    for folder, consume in ((new_dir, True), (cur_dir, False)):
        for path in sorted(p for p in folder.iterdir() if p.is_file()):
            if path.name != wanted:
                continue
            try:
                parsed = _parse_message(path)
            except FileNotFoundError:
                continue
            if consume:
                dest = cur_dir / path.name
                try:
                    path.replace(dest)
                except FileNotFoundError:
                    continue
                parsed["id"] = dest.name
            else:
                parsed["id"] = path.name
            return [parsed]
    return []


def _consume_new(new_dir: Path, cur_dir: Path) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for path in sorted(p for p in new_dir.iterdir() if p.is_file()):
        try:
            parsed = _parse_message(path)
            dest = cur_dir / path.name
            path.replace(dest)
        except FileNotFoundError:
            continue
        parsed["id"] = dest.name
        messages.append(parsed)
    return messages


def _unique_name() -> str:
    return f"{int(time.time())}.{secrets.token_hex(8)}"


def _format_message(identity_id: str, to: str, subject: str, body: str) -> str:
    subj = (subject or "").replace("\n", " ").replace("\r", " ")
    return f"From: {identity_id}\nTo: {to}\nSubject: {subj}\n\n{body}"


def _parse_message(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    headers, _, body = text.partition("\n\n")
    fields: dict[str, str] = {"from": "", "to": "", "subject": "", "body": body}
    for line in headers.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip().lower()] = value.strip()
    fields["id"] = path.name
    return fields

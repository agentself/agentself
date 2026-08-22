from __future__ import annotations

import builtins
import email
import email.policy
import imaplib
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from agentself.backends.email.contract import (
    MailboxAccess,
    MailboxError,
    mailbox_view,
    require_addr,
    require_secret,
    setup_needed,
)
from agentself.internal.files import VaultBusy, exclusive
from agentself.internal.log import Log
from agentself.internal.names import require_safe_token
from agentself.internal.setup import address_option, credential_option

_IMAP_PORT = 993
_SMTP_PORT = 587

ImapOpener = Callable[[str, int, str], "ImapBox"]
SmtpOpener = Callable[[str, int, str], "SmtpBox"]


class ImapBox(Protocol):
    def login(self, user: str, password: str) -> None: ...

    def uids(self, *, unseen_only: bool = False) -> list[str]: ...

    def fetch(self, uid: str, *, headers_only: bool = False) -> bytes: ...

    def mark_seen(self, uid: str) -> None: ...

    def logout(self) -> None: ...


class SmtpBox(Protocol):
    def login(self, user: str, password: str) -> None: ...

    def send(self, from_addr: str, to: str, payload: bytes) -> None: ...

    def quit(self) -> None: ...


class ImapMailboxAccess(MailboxAccess):
    """IMAP receive/list plus SMTP send. One bind; caller never names the protocol.

    Hosts default to imap.{domain} and smtp.{domain} of email.address.
    Override with a shared mail host or split IMAP/SMTP hosts. Ports default
    to 993/587; TLS is implied by the port (993/465 SSL, 143/587 STARTTLS).
    """

    def __init__(
        self,
        vault_root: Path,
        log: Log,
        *,
        domain: str = "",
        mail_host: str = "",
        imap_host: str = "",
        smtp_host: str = "",
        imap_port: str = "",
        smtp_port: str = "",
        mail_user: str = "",
        imap_opener: ImapOpener | None = None,
        smtp_opener: SmtpOpener | None = None,
    ) -> None:
        self._root = Path(vault_root)
        self._log = log
        self._domain = (domain or "").strip()
        self._mail_host = (mail_host or "").strip()
        self._imap_host = (imap_host or "").strip()
        self._smtp_host = (smtp_host or "").strip()
        self._imap_port = (imap_port or "").strip()
        self._smtp_port = (smtp_port or "").strip()
        self._mail_user = (mail_user or "").strip()
        self._imap_opener = imap_opener
        self._smtp_opener = smtp_opener

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
        from_addr = self._inbox(address)
        host, port, mode = self._endpoint("smtp", from_addr)
        payload = _rfc822(from_addr, to, subject, body)
        box = self._smtp(host, port, mode)
        try:
            box.login(self._user(from_addr), send_token)
            box.send(from_addr, to, payload)
        except (OSError, TimeoutError, smtplib.SMTPException) as exc:
            raise MailboxError("rpc failed") from exc
        finally:
            _close(box.quit)
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
        inbox = self._inbox(address)
        try:
            with exclusive(self._root):
                box = self._imap_login(inbox, send_token)
                try:
                    wanted = (message_id or "").strip()
                    if wanted:
                        messages = self._take_one(box, wanted)
                    else:
                        messages = []
                        for uid in box.uids(unseen_only=True):
                            parsed = _take(box, uid, mark=False)
                            if parsed is not None:
                                messages.append(parsed)
                        for parsed in messages:
                            try:
                                box.mark_seen(parsed["id"])
                            except (OSError, TimeoutError, imaplib.IMAP4.error):
                                pass
                except (OSError, TimeoutError, imaplib.IMAP4.error) as exc:
                    raise MailboxError("rpc failed") from exc
                finally:
                    _close(box.logout)
        except VaultBusy as exc:
            raise MailboxError("rpc failed") from exc
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
        inbox = self._inbox(address)
        box = self._imap_login(inbox, send_token)
        try:
            items: list[dict[str, str]] = []
            for uid in box.uids(unseen_only=False):
                parsed = _take(box, uid, mark=False, headers_only=True)
                if parsed is not None:
                    items.append(
                        {
                            "id": parsed["id"],
                            "from": parsed.get("from", ""),
                            "to": parsed.get("to", ""),
                            "subject": parsed.get("subject", ""),
                        }
                    )
        except (OSError, TimeoutError, imaplib.IMAP4.error) as exc:
            raise MailboxError("rpc failed") from exc
        finally:
            _close(box.logout)
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
            return mailbox_view(self._inbox(wanted), owned_address=True)
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
        needed: list[dict[str, object]] = []
        if not wanted:
            needed.append(address_option(required=True))
        if not token:
            needed.append(credential_option(required=True, help="Mailbox credential"))
        if needed:
            self._log.record("mailbox_connect", principal_id, None, "error")
            return setup_needed(needed)
        token = require_secret(token)
        inbox = self._inbox(wanted)
        box = self._imap_login(inbox, token)
        _close(box.logout)
        self._log.record("mailbox_connect", principal_id, None, "ok")
        return mailbox_view(inbox, owned_address=True)

    def _inbox(self, address: str | None) -> str:
        wanted = (address or "").strip()
        if not wanted:
            raise MailboxError("no inbox")
        try:
            require_addr(wanted)
        except MailboxError:
            raise MailboxError("no inbox") from None
        return wanted

    def _user(self, address: str) -> str:
        user = self._mail_user or address
        if not user or "\n" in user or "\r" in user:
            raise MailboxError("no inbox")
        return user

    def _endpoint(self, kind: str, address: str) -> tuple[str, int, str]:
        host = _require_host(self._host(kind, address))
        port = _port(
            self._imap_port if kind == "imap" else self._smtp_port,
            _IMAP_PORT if kind == "imap" else _SMTP_PORT,
        )
        return host, port, _tls_mode(kind, port)

    def _host(self, kind: str, address: str) -> str:
        explicit = self._imap_host if kind == "imap" else self._smtp_host
        if explicit:
            return explicit
        if self._mail_host:
            return self._mail_host
        domain = address.rsplit("@", 1)[-1].strip().lower()
        if not domain or domain == address.lower():
            raise MailboxError("no inbox")
        return f"{kind}.{domain}"

    def _imap_login(self, address: str, token: str) -> ImapBox:
        token = require_secret(token)
        host, port, mode = self._endpoint("imap", address)
        box = self._imap(host, port, mode)
        try:
            box.login(self._user(address), token)
        except (OSError, TimeoutError, imaplib.IMAP4.error) as exc:
            _close(box.logout)
            raise MailboxError("rpc failed") from exc
        return box

    def _imap(self, host: str, port: int, mode: str) -> ImapBox:
        opener = self._imap_opener or _default_imap_opener
        try:
            return opener(host, port, mode)
        except (OSError, TimeoutError, imaplib.IMAP4.error) as exc:
            raise MailboxError("rpc failed") from exc

    def _smtp(self, host: str, port: int, mode: str) -> SmtpBox:
        opener = self._smtp_opener or _default_smtp_opener
        try:
            return opener(host, port, mode)
        except (OSError, TimeoutError, smtplib.SMTPException) as exc:
            raise MailboxError("rpc failed") from exc

    def _take_one(self, box: ImapBox, wanted: str) -> builtins.list[dict[str, str]]:
        if not _uid_ok(wanted):
            return []
        parsed = _take(box, wanted, mark=True)
        if parsed is None:
            return []
        return [parsed]


def _take(
    box: ImapBox,
    uid: str,
    *,
    mark: bool,
    headers_only: bool = False,
) -> dict[str, str] | None:
    if not _uid_ok(uid):
        return None
    raw = box.fetch(uid, headers_only=headers_only)
    if not raw:
        return None
    parsed = _parse(raw)
    parsed["id"] = uid
    if mark:
        box.mark_seen(uid)
    return parsed


def _parse(raw: bytes) -> dict[str, str]:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    return {
        "id": "",
        "from": _hdr(msg, "from"),
        "to": _hdr(msg, "to"),
        "subject": _hdr(msg, "subject"),
        "body": _body_of(msg),
    }


def _hdr(msg: email.message.Message, name: str) -> str:
    value = msg.get(name)
    if value is None:
        return ""
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def _body_of(msg: email.message.Message) -> str:
    getter = getattr(msg, "get_body", None)
    if callable(getter):
        part = getter(preferencelist=("plain",))
        if part is not None:
            text = _part_text(part)
            if text is not None:
                return text
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                text = _part_text(part)
                if text is not None:
                    return text
        return ""
    text = _part_text(msg)
    return text if text is not None else ""


def _part_text(part: email.message.Message) -> str | None:
    get_content = getattr(part, "get_content", None)
    if callable(get_content):
        try:
            content = get_content()
        except (LookupError, TypeError, ValueError, UnicodeError):
            content = None
        if isinstance(content, str):
            return content
    payload = part.get_payload(decode=True)
    if isinstance(payload, (bytes, bytearray)):
        charset = part.get_content_charset() or "utf-8"
        try:
            return bytes(payload).decode(charset, errors="replace")
        except LookupError:
            return bytes(payload).decode("utf-8", errors="replace")
    if isinstance(payload, str):
        return payload
    return None


def _rfc822(from_addr: str, to: str, subject: str, body: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = (subject or "").replace("\r", " ").replace("\n", " ")
    msg.set_content(body or "")
    return msg.as_bytes()


def _require_host(host: str) -> str:
    value = (host or "").strip()
    if not value or any(ch.isspace() for ch in value) or "\x00" in value:
        raise MailboxError("rpc failed")
    return value


def _port(value: str, default: int) -> int:
    text = (value or "").strip()
    if not text:
        return default
    if not text.isdigit():
        raise MailboxError("rpc failed")
    port = int(text)
    if port < 1 or port > 65535:
        raise MailboxError("rpc failed")
    return port


def _tls_mode(kind: str, port: int) -> str:
    if kind == "imap":
        return "starttls" if port == 143 else "ssl"
    return "ssl" if port == 465 else "starttls"


def _uid_ok(uid: str) -> bool:
    return bool(uid) and uid.isdigit()


def _close(fn: Callable[[], object]) -> None:
    try:
        fn()
    except Exception:
        return


def _payload(data: object) -> bytes:
    if not data:
        return b""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if not isinstance(data, (list, tuple)):
        return b""
    best = b""
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2:
            part = item[1]
            if isinstance(part, (bytes, bytearray)) and len(part) >= len(best):
                best = bytes(part)
        elif isinstance(item, (bytes, bytearray)):
            if item.strip() in (b")", b""):
                continue
            if len(item) >= len(best):
                best = bytes(item)
    return best


def _imap_ok(typ: str) -> None:
    if typ != "OK":
        raise OSError("rpc failed")


class _StdImap:
    def __init__(self, client: imaplib.IMAP4) -> None:
        self._c = client

    def login(self, user: str, password: str) -> None:
        try:
            typ, _data = self._c.login(user, password)
            _imap_ok(typ)
            selected, _inbox = self._c.select("INBOX")
            _imap_ok(selected)
        except imaplib.IMAP4.error as exc:
            raise OSError("rpc failed") from exc

    def uids(self, *, unseen_only: bool = False) -> list[str]:
        try:
            criterion = "UNSEEN" if unseen_only else "ALL"
            typ, data = self._c.uid("SEARCH", criterion)
            _imap_ok(typ)
        except imaplib.IMAP4.error as exc:
            raise OSError("rpc failed") from exc
        raw = data[0] if data else b""
        if not raw:
            return []
        if isinstance(raw, str):
            text = raw
        else:
            text = bytes(raw).decode("ascii", errors="replace")
        return [part for part in text.split() if _uid_ok(part)]

    def fetch(self, uid: str, *, headers_only: bool = False) -> bytes:
        spec = "(BODY.PEEK[HEADER])" if headers_only else "(RFC822)"
        try:
            typ, data = self._c.uid("FETCH", uid, spec)
            _imap_ok(typ)
        except imaplib.IMAP4.error as exc:
            raise OSError("rpc failed") from exc
        return _payload(data)

    def mark_seen(self, uid: str) -> None:
        try:
            typ, _data = self._c.uid("STORE", uid, "+FLAGS", r"(\Seen)")
            _imap_ok(typ)
        except imaplib.IMAP4.error as exc:
            raise OSError("rpc failed") from exc

    def logout(self) -> None:
        try:
            self._c.close()
        except Exception:
            pass
        try:
            self._c.logout()
        except Exception:
            pass


class _StdSmtp:
    def __init__(self, client: smtplib.SMTP) -> None:
        self._c = client

    def login(self, user: str, password: str) -> None:
        self._c.login(user, password)

    def send(self, from_addr: str, to: str, payload: bytes) -> None:
        self._c.sendmail(from_addr, [to], payload)

    def quit(self) -> None:
        try:
            self._c.quit()
        except Exception:
            try:
                self._c.close()
            except Exception:
                return


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def _default_imap_opener(host: str, port: int, mode: str) -> _StdImap:
    ctx = _ssl_context()
    client: imaplib.IMAP4 | None = None
    try:
        if mode == "ssl":
            client = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=15)
        else:
            client = imaplib.IMAP4(host, port, timeout=15)
            client.starttls(ssl_context=ctx)
        return _StdImap(client)
    except Exception:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass
        raise


def _default_smtp_opener(host: str, port: int, mode: str) -> _StdSmtp:
    ctx = _ssl_context()
    client: smtplib.SMTP | None = None
    try:
        if mode == "ssl":
            client = smtplib.SMTP_SSL(host, port, timeout=15, context=ctx)
        else:
            client = smtplib.SMTP(host, port, timeout=15)
            client.ehlo()
            client.starttls(context=ctx)
            client.ehlo()
        return _StdSmtp(client)
    except Exception:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        raise

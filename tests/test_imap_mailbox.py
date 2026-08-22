"""IMAP/SMTP mailbox: one bind, derived hosts, fail-closed, no credential leak."""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

import pytest

from agentself.backends.email.contract import MailboxError
from agentself.backends.email.factory import MailboxAccessFactory
from agentself.backends.email.imap import ImapMailboxAccess
from agentself.internal.log import MemoryLog

from tests.support import cli_env, run_cli

CANARY = "CANARY-IMAP-PASSWORD-DO-NOT-LEAK"
PRINCIPAL = "desk"
ADDRESS = "bot@fastmail.com"
TO = "someone@example.com"


def _raw(
    from_addr: str = "a@example.com",
    to: str = ADDRESS,
    subject: str = "inbox-subject",
    body: str = "inbox-body",
    uid: str = "1",
) -> bytes:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{uid}@example.com>"
    msg.set_content(body)
    return msg.as_bytes()


class FakeImap:
    def __init__(self, messages: list[dict[str, object]] | None = None) -> None:
        self.messages = messages or []
        self.opened: list[tuple[str, int, str]] = []
        self.logins: list[tuple[str, str]] = []
        self.logout_count = 0
        self.fail_open: BaseException | None = None
        self.fail_login: BaseException | None = None

    def opener(self, host: str, port: int, mode: str) -> FakeImap:
        self.opened.append((host, port, mode))
        if self.fail_open is not None:
            raise self.fail_open
        return self

    def login(self, user: str, password: str) -> None:
        self.logins.append((user, password))
        if self.fail_login is not None:
            raise self.fail_login

    def uids(self, *, unseen_only: bool = False) -> list[str]:
        out: list[str] = []
        for item in self.messages:
            if unseen_only and item.get("seen"):
                continue
            out.append(str(item["uid"]))
        return out

    def fetch(self, uid: str, *, headers_only: bool = False) -> bytes:
        del headers_only
        for item in self.messages:
            if str(item["uid"]) == uid:
                raw = item.get("raw")
                return raw if isinstance(raw, (bytes, bytearray)) else b""
        return b""

    def mark_seen(self, uid: str) -> None:
        for item in self.messages:
            if str(item["uid"]) == uid:
                item["seen"] = True
                return

    def logout(self) -> None:
        self.logout_count += 1


class FakeSmtp:
    def __init__(self) -> None:
        self.opened: list[tuple[str, int, str]] = []
        self.logins: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str, bytes]] = []
        self.quit_count = 0
        self.fail_open: BaseException | None = None
        self.fail_login: BaseException | None = None

    def opener(self, host: str, port: int, mode: str) -> FakeSmtp:
        self.opened.append((host, port, mode))
        if self.fail_open is not None:
            raise self.fail_open
        return self

    def login(self, user: str, password: str) -> None:
        self.logins.append((user, password))
        if self.fail_login is not None:
            raise self.fail_login

    def send(self, from_addr: str, to: str, payload: bytes) -> None:
        self.sent.append((from_addr, to, payload))

    def quit(self) -> None:
        self.quit_count += 1


def _box(
    vault: Path,
    log: MemoryLog,
    imap: FakeImap,
    smtp: FakeSmtp,
    **kwargs: str,
) -> ImapMailboxAccess:
    return ImapMailboxAccess(
        vault,
        log,
        imap_opener=imap.opener,
        smtp_opener=smtp.opener,
        **kwargs,
    )


def _secret_absent(log: MemoryLog, exc: BaseException | None = None) -> None:
    blobs = [log.rendered()]
    if exc is not None:
        blobs.append(str(exc))
        blobs.append(repr(exc))
    for blob in blobs:
        assert CANARY not in blob


def _no_local_mail(vault: Path) -> None:
    for rel in (
        Path("identities") / PRINCIPAL / "outbox",
        Path("identities") / PRINCIPAL / "maildir",
        Path("identities") / PRINCIPAL / "routing",
        Path("identities") / PRINCIPAL / "imap",
    ):
        path = vault / rel
        assert not path.exists() or not any(path.rglob("*"))


def test_no_token_zero_connections(vault):
    log = MemoryLog()
    imap = FakeImap()
    smtp = FakeSmtp()
    mb = _box(vault, log, imap, smtp)
    with pytest.raises(MailboxError, match="send failed") as send_err:
        mb.send(PRINCIPAL, TO, "s", "b", address=ADDRESS)
    with pytest.raises(MailboxError, match="recv failed") as recv_err:
        mb.recv(PRINCIPAL, address=ADDRESS)
    with pytest.raises(MailboxError, match="list failed") as list_err:
        mb.list(PRINCIPAL, address=ADDRESS)
    assert smtp.opened == []
    assert imap.opened == []
    _no_local_mail(vault)
    for exc in (send_err.value, recv_err.value, list_err.value):
        _secret_absent(log, exc)


def test_no_address_zero_connections(vault):
    log = MemoryLog()
    imap = FakeImap()
    smtp = FakeSmtp()
    mb = _box(vault, log, imap, smtp)
    with pytest.raises(MailboxError, match="no inbox"):
        mb.send(PRINCIPAL, TO, "s", "b", send_token=CANARY)
    with pytest.raises(MailboxError, match="no inbox"):
        mb.recv(PRINCIPAL, send_token=CANARY)
    with pytest.raises(MailboxError, match="no inbox"):
        mb.list(PRINCIPAL, send_token=CANARY)
    assert smtp.opened == []
    assert imap.opened == []
    _secret_absent(log)


def test_describe_uses_address_hold_never_principal_at_domain(vault):
    log = MemoryLog()
    mb = _box(vault, log, FakeImap(), FakeSmtp(), domain="fastmail.com")
    empty = mb.describe(PRINCIPAL)
    assert empty == {
        "address": None,
        "owned_address": False,
        "needs_domain": False,
    }
    owned = mb.describe(PRINCIPAL, address=ADDRESS, send_token=CANARY)
    assert owned == {
        "address": ADDRESS,
        "owned_address": True,
        "needs_domain": False,
    }
    assert f"{PRINCIPAL}@fastmail.com" not in str(owned)
    assert f"{PRINCIPAL}@fastmail.com" not in str(empty)


def test_send_derives_smtp_host_from_address_domain(vault):
    log = MemoryLog()
    imap = FakeImap()
    smtp = FakeSmtp()
    mb = _box(vault, log, imap, smtp)
    mb.send(PRINCIPAL, TO, "hello", "body-text", send_token=CANARY, address=ADDRESS)
    assert smtp.opened == [("smtp.fastmail.com", 587, "starttls")]
    assert imap.opened == []
    assert smtp.logins == [(ADDRESS, CANARY)]
    assert smtp.quit_count == 1
    assert len(smtp.sent) == 1
    from_addr, to, payload = smtp.sent[0]
    assert from_addr == ADDRESS
    assert to == TO
    text = payload.decode("utf-8")
    assert ADDRESS in text
    assert TO in text
    assert "hello" in text
    assert "body-text" in text
    assert PRINCIPAL not in text.split("From:", 1)[-1].splitlines()[0]
    _secret_absent(log)
    _no_local_mail(vault)


def test_mail_host_is_shared_and_split_hosts_override(vault):
    log = MemoryLog()
    imap = FakeImap()
    smtp = FakeSmtp()
    shared = _box(vault, log, imap, smtp, mail_host="mail.example.com")
    shared.send(PRINCIPAL, TO, "hello", "body-text", send_token=CANARY, address=ADDRESS)
    assert smtp.opened == [("mail.example.com", 587, "starttls")]
    imap.messages = [
        {
            "uid": "12",
            "raw": _raw(uid="12"),
            "seen": False,
        }
    ]
    split = _box(
        vault,
        log,
        imap,
        smtp,
        mail_host="mail.example.com",
        imap_host="outlook.office365.com",
        smtp_host="smtp.office365.com",
    )
    split.list(PRINCIPAL, send_token=CANARY, address=ADDRESS)
    assert imap.opened == [("outlook.office365.com", 993, "ssl")]
    split.send(PRINCIPAL, TO, "hello", "body-text", send_token=CANARY, address=ADDRESS)
    assert smtp.opened[-1] == ("smtp.office365.com", 587, "starttls")


def test_ports_imply_tls_mode_and_mail_user_overrides_login(vault):
    log = MemoryLog()
    imap = FakeImap([{"uid": "7", "raw": _raw(uid="7"), "seen": False}])
    smtp = FakeSmtp()
    mb = _box(
        vault,
        log,
        imap,
        smtp,
        imap_host="mail.example.com",
        smtp_host="mail.example.com",
        imap_port="143",
        smtp_port="465",
        mail_user="bot",
    )
    mb.list(PRINCIPAL, send_token=CANARY, address=ADDRESS)
    mb.send(PRINCIPAL, TO, "hello", "body", send_token=CANARY, address=ADDRESS)
    assert imap.opened == [("mail.example.com", 143, "starttls")]
    assert smtp.opened == [("mail.example.com", 465, "ssl")]
    assert imap.logins == [("bot", CANARY)]
    assert smtp.logins == [("bot", CANARY)]
    _secret_absent(log)


def test_recv_unseen_then_empty_list_still_shows(vault):
    log = MemoryLog()
    imap = FakeImap(
        [
            {
                "uid": "10",
                "raw": _raw(subject="old", body="seen-body", uid="10"),
                "seen": True,
            },
            {
                "uid": "11",
                "raw": _raw(subject="inbox-subject", body="inbox-body", uid="11"),
                "seen": False,
            },
        ]
    )
    smtp = FakeSmtp()
    mb = _box(vault, log, imap, smtp)
    listed = mb.list(PRINCIPAL, send_token=CANARY, address=ADDRESS)
    assert [item["id"] for item in listed] == ["10", "11"]
    assert all("body" not in item for item in listed)
    assert listed[1]["subject"] == "inbox-subject"
    recvd = mb.recv(PRINCIPAL, send_token=CANARY, address=ADDRESS)
    assert len(recvd) == 1
    assert recvd[0]["id"] == "11"
    assert recvd[0]["subject"] == "inbox-subject"
    assert recvd[0]["body"].strip() == "inbox-body"
    assert recvd[0]["from"] == "a@example.com"
    assert imap.messages[1]["seen"] is True
    assert mb.recv(PRINCIPAL, send_token=CANARY, address=ADDRESS) == []
    still = mb.list(PRINCIPAL, send_token=CANARY, address=ADDRESS)
    assert [item["id"] for item in still] == ["10", "11"]
    again = mb.recv(PRINCIPAL, send_token=CANARY, address=ADDRESS, message_id="10")
    assert len(again) == 1
    assert again[0]["id"] == "10"
    assert "seen-body" in again[0]["body"]
    missing = mb.recv(PRINCIPAL, send_token=CANARY, address=ADDRESS, message_id="nope")
    assert missing == []
    _secret_absent(log)
    assert imap.logout_count >= 1


def test_transport_error_is_rpc_failed_without_secret(vault):
    log = MemoryLog()
    imap = FakeImap()
    smtp = FakeSmtp()
    smtp.fail_open = OSError("connection refused")
    mb = _box(vault, log, imap, smtp)
    with pytest.raises(MailboxError, match="rpc failed") as send_err:
        mb.send(PRINCIPAL, TO, "s", "b", send_token=CANARY, address=ADDRESS)
    imap.fail_login = OSError("auth failed")
    with pytest.raises(MailboxError, match="rpc failed") as recv_err:
        mb.recv(PRINCIPAL, send_token=CANARY, address=ADDRESS)
    _secret_absent(log, send_err.value)
    _secret_absent(log, recv_err.value)
    assert (
        CANARY not in str(send_err.value.__cause__)
        if send_err.value.__cause__
        else True
    )


def test_invalid_address_and_port_fail_closed(vault):
    log = MemoryLog()
    imap = FakeImap()
    smtp = FakeSmtp()
    mb = _box(vault, log, imap, smtp)
    with pytest.raises(MailboxError, match="no inbox"):
        mb.send(PRINCIPAL, TO, "s", "b", send_token=CANARY, address="not-an-address")
    assert smtp.opened == []
    bad_port = _box(vault, log, imap, smtp, smtp_port="not-a-port")
    with pytest.raises(MailboxError, match="rpc failed"):
        bad_port.send(PRINCIPAL, TO, "s", "b", send_token=CANARY, address=ADDRESS)
    assert smtp.opened == []


def test_compose_forwards_imap_env_knobs(vault, monkeypatch):
    seen: dict[str, str] = {}
    real = MailboxAccessFactory.__init__

    def wrapped(self, vault_root, log, **kwargs):
        seen.update(kwargs)
        return real(self, vault_root, log, **kwargs)

    monkeypatch.setattr(MailboxAccessFactory, "__init__", wrapped)
    monkeypatch.setenv("AGENTSELF_MAIL_HOST", "mail.example.com")
    monkeypatch.setenv("AGENTSELF_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("AGENTSELF_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("AGENTSELF_MAIL_USER", "bot")
    monkeypatch.setenv("AGENTSELF_IMAP_PORT", "993")
    monkeypatch.setenv("AGENTSELF_SMTP_PORT", "465")
    from agentself.compose import compose

    compose(vault, email_backend="imap")
    assert seen["mail_host"] == "mail.example.com"
    assert seen["imap_host"] == "imap.example.com"
    assert seen["smtp_host"] == "smtp.example.com"
    assert seen["mail_user"] == "bot"
    assert seen["imap_port"] == "993"
    assert seen["smtp_port"] == "465"


def test_factory_mounts_imap_with_host_knobs(vault):
    log = MemoryLog()
    factory = MailboxAccessFactory(
        vault,
        log,
        mail_host="mail.example.com",
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        mail_user="bot",
        imap_port="993",
        smtp_port="587",
    )
    mb = factory.for_binding("imap")
    assert isinstance(mb, ImapMailboxAccess)
    assert mb._mail_host == "mail.example.com"
    assert mb._imap_host == "imap.example.com"
    assert mb._smtp_host == "smtp.example.com"
    assert mb._mail_user == "bot"


def test_cli_imap_bind_is_usable_and_fails_closed(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init", "--email", "imap"], env)
    assert start.returncode == 0, start.stderr
    assert "email_backend: imap" in start.stdout
    shown = run_cli(["email", "show"], env)
    assert shown.returncode == 0, shown.stderr
    assert shown.stdout.strip() == "not configured"
    sent = run_cli(["email", "send", TO, "hello", "body"], env)
    assert sent.returncode == 1, sent.stdout + sent.stderr
    assert "Traceback" not in sent.stderr
    assert CANARY not in sent.stdout + sent.stderr
    lines = [line for line in sent.stderr.splitlines() if line.strip()]
    assert lines[0] == "error: email send needs a domain and send credentials"
    assert lines[1] == "next: agentself backends email"
    backends = run_cli(["backends", "email"], env)
    assert backends.returncode == 0, backends.stderr
    assert "imap" in backends.stdout
    assert "email.address" in backends.stdout
    assert "AGENTSELF_MAIL_HOST" in backends.stdout

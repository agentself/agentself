"""Own-principal email on maildir/file; foreign refused before MailboxAccess."""

from __future__ import annotations

import pytest

from agentself.internal.custody.errors import EmailSendNotReady, Refused
from agentself.internal.files import identity_home
from agentself.internal.log import MemoryLog

from tests.maildir_mailbox import MaildirMailboxAccess
from tests.support import (
    build_app,
    cli_env,
    plant_email,
    run_cli,
    setup_principal,
)


def test_own_email_send_recv_list_maildir(app, monkeypatch):
    app.keys["P"] = setup_principal(app.vault, "P", store="sops")
    app.bind(monkeypatch, "P")
    app.gateway.enroll("sops")

    with pytest.raises(EmailSendNotReady, match="domain and send credentials"):
        app.gateway.email_send("someone@example.com", "hello", "body-text")
    outbox = identity_home(app.vault, "P") / "outbox"
    assert not outbox.exists() or not list(outbox.iterdir())

    plant_email(
        app.vault,
        "P",
        from_addr="a@example.com",
        subject="inbox-subject",
        body="inbox-body",
    )
    listed = app.gateway.email_list()
    assert any(item["subject"] == "inbox-subject" for item in listed)
    recvd = app.gateway.email_recv()
    assert len(recvd) == 1
    assert recvd[0]["subject"] == "inbox-subject"
    assert recvd[0]["body"].strip() == "inbox-body"
    assert app.gateway.email_recv() == []
    still = app.gateway.email_list()
    assert any(item["subject"] == "inbox-subject" for item in still)


def test_email_send_with_address_hold_and_token_writes_maildir_outbox(
    vault, monkeypatch
):
    app = build_app(vault)
    app.keys["P"] = setup_principal(app.vault, "P", store="sops")
    app.bind(monkeypatch, "P")
    app.gateway.enroll("sops")
    app.gateway.seal("email.credential", "tok")
    app.gateway.seal("email.address", "inbox@example.com")
    app.gateway.email_send("someone@example.com", "hello", "body-text")
    outbox = identity_home(app.vault, "P") / "outbox"
    sent = list(outbox.iterdir())
    assert sent
    text = sent[0].read_text(encoding="utf-8")
    assert "someone@example.com" in text
    assert "hello" in text


def test_email_send_maildir_domain_and_token_without_address_fails_closed(
    vault, monkeypatch
):
    app = build_app(vault, mail_domain="example.com")
    app.keys["P"] = setup_principal(app.vault, "P", store="sops")
    app.bind(monkeypatch, "P")
    app.gateway.enroll("sops")
    app.gateway.seal("email.credential", "tok")
    with pytest.raises(EmailSendNotReady):
        app.gateway.email_send("someone@example.com", "hello", "body-text")
    outbox = identity_home(app.vault, "P") / "outbox"
    assert not outbox.exists() or not list(outbox.iterdir())


def test_email_send_with_domain_without_token_fails_closed(vault, monkeypatch):
    app = build_app(vault, mail_domain="example.com")
    app.keys["P"] = setup_principal(app.vault, "P", store="sops")
    app.bind(monkeypatch, "P")
    app.gateway.enroll("sops")
    with pytest.raises(EmailSendNotReady):
        app.gateway.email_send("someone@example.com", "hello", "body-text")
    outbox = identity_home(app.vault, "P") / "outbox"
    assert not outbox.exists() or not list(outbox.iterdir())


def test_cli_email_send_without_domain_fails_closed(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    sent = run_cli(["email", "send", "someone@example.com", "hello", "body"], env)
    assert sent.returncode == 1, sent.stdout + sent.stderr
    assert "Traceback" not in sent.stderr
    assert "Traceback" not in sent.stdout
    lines = [line for line in sent.stderr.splitlines() if line.strip()]
    assert lines[0] == "error: email send needs a domain and send credentials"
    assert lines[1] == "next: agentself backends email"
    outbox = identity_home(vault, "agent") / "outbox"
    assert not outbox.exists() or not list(outbox.iterdir())


def test_cli_email_send_domain_without_token_fails_closed(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    configured = run_cli(["email", "connect", "--domain", "example.com"], env)
    assert configured.returncode == 2, configured.stdout + configured.stderr
    assert "unrecognized arguments: --domain" in configured.stderr
    sent = run_cli(["email", "send", "someone@example.com", "hello", "body"], env)
    assert sent.returncode == 1, sent.stdout + sent.stderr
    lines = [line for line in sent.stderr.splitlines() if line.strip()]
    assert lines[0] == "error: email send needs a domain and send credentials"
    assert lines[1] == "next: agentself backends email"


def test_foreign_email_refused_before_mailbox_access(app, monkeypatch):
    app.keys["P"] = setup_principal(app.vault, "P", store="sops")
    app.keys["Q"] = setup_principal(app.vault, "Q", store="sops")
    app.bind(monkeypatch, "P")
    app.gateway.enroll("sops")
    app.bind(monkeypatch, "Q")
    app.gateway.enroll("sops")

    app.bind(monkeypatch, "Q")
    before_calls = list(app.mailboxes.calls)
    before_bind = list(app.mailboxes.for_binding_calls)
    before_store = list(app.stores.calls)

    with pytest.raises(Refused):
        app.gateway.email_send("a@b.c", "s", "b", hold_owner="P")
    with pytest.raises(Refused):
        app.gateway.email_recv(hold_owner="P")
    with pytest.raises(Refused):
        app.gateway.email_list(hold_owner="P")
    with pytest.raises(Refused):
        app.gateway.email_connect(hold_owner="P")

    assert app.mailboxes.calls == before_calls
    assert app.mailboxes.for_binding_calls == before_bind
    assert app.stores.calls == before_store


def test_maildir_recv_message_id_reads_cur(vault):
    log = MemoryLog()
    mb = MaildirMailboxAccess(vault, log)
    cur = identity_home(vault, "P") / "maildir" / "cur"
    new = identity_home(vault, "P") / "maildir" / "new"
    cur.mkdir(mode=0o700, parents=True)
    new.mkdir(mode=0o700, parents=True)
    name = "already.seen"
    (cur / name).write_text(
        "From: a@example.com\nTo: P@local\nSubject: old\n\nbody-old\n",
        encoding="utf-8",
    )
    (new / "fresh.new").write_text(
        "From: b@example.com\nTo: P@local\nSubject: new\n\nbody-new\n",
        encoding="utf-8",
    )
    got = mb.recv("P", message_id=name)
    assert len(got) == 1
    assert got[0]["id"] == name
    assert "body-old" in got[0]["body"]
    assert (cur / name).is_file()
    recvd = mb.recv("P")
    assert len(recvd) == 1
    assert recvd[0]["id"] == "fresh.new"
    assert not (new / "fresh.new").exists()
    assert (cur / "fresh.new").is_file()
    assert mb.recv("P") == []
    again = mb.recv("P", message_id="fresh.new")
    assert len(again) == 1
    assert again[0]["id"] == "fresh.new"

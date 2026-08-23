"""Bound-identity email on maildir; another identity does not see that mail."""

from __future__ import annotations

import pytest

from agentself.internal.custody.errors import EmailSendNotReady
from agentself.internal.files import identity_home
from agentself.internal.log import MemoryLog

from tests.maildir_mailbox import MaildirMailboxAccess
from tests.support import (
    build_app,
    cli_env,
    init_identity,
    plant_email,
    run_cli,
)


def test_own_email_send_receive_list_maildir(app, monkeypatch):
    init_identity(app, monkeypatch)

    with pytest.raises(EmailSendNotReady, match="domain and send credentials"):
        app.client.email_send("someone@example.com", "hello", "body-text")
    outbox = identity_home(app.vault, "P") / "outbox"
    assert not outbox.exists() or not list(outbox.iterdir())

    plant_email(
        app.vault,
        "P",
        from_addr="a@example.com",
        subject="inbox-subject",
        body="inbox-body",
    )
    listed = app.client.email_list()
    assert any(item["subject"] == "inbox-subject" for item in listed)
    recvd = app.client.email_receive()
    assert len(recvd) == 1
    assert recvd[0]["subject"] == "inbox-subject"
    assert recvd[0]["body"].strip() == "inbox-body"
    assert app.client.email_receive() == []
    still = app.client.email_list()
    assert any(item["subject"] == "inbox-subject" for item in still)


def test_email_send_with_address_secret_and_token_writes_maildir_outbox(
    vault, monkeypatch
):
    app = build_app(vault)
    init_identity(app, monkeypatch)
    app.client.create("email.credential", "tok")
    app.client.create("email.address", "inbox@example.com")
    app.client.email_send("someone@example.com", "hello", "body-text")
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
    init_identity(app, monkeypatch)
    app.client.create("email.credential", "tok")
    with pytest.raises(EmailSendNotReady):
        app.client.email_send("someone@example.com", "hello", "body-text")
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
    assert "error:" in sent.stderr
    assert "agentself backends email" in sent.stderr
    outbox = identity_home(vault, "agent") / "outbox"
    assert not outbox.exists() or not list(outbox.iterdir())


def test_q_email_does_not_see_p_mail(app, monkeypatch):
    init_identity(app, monkeypatch, "P")
    plant_email(
        app.vault, "P", from_addr="a@example.com", subject="for-p", body="p-body"
    )
    init_identity(app, monkeypatch, "Q")
    assert app.client.email_list() == []
    assert app.client.email_receive() == []
    app.bind(monkeypatch, "P")
    listed = app.client.email_list()
    assert listed and listed[0]["subject"] == "for-p"


def test_maildir_receive_message_id_reads_cur(vault):
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
    got = mb.receive("P", message_id=name)
    assert len(got) == 1
    assert got[0]["id"] == name
    assert "body-old" in got[0]["body"]
    assert (cur / name).is_file()
    recvd = mb.receive("P")
    assert len(recvd) == 1
    assert recvd[0]["id"] == "fresh.new"
    assert not (new / "fresh.new").exists()
    assert (cur / "fresh.new").is_file()
    assert mb.receive("P") == []
    again = mb.receive("P", message_id="fresh.new")
    assert len(again) == 1
    assert again[0]["id"] == "fresh.new"

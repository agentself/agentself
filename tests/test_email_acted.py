from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from agentself.internal.files import identity_home
from agentself.internal.mail_state import MailRefState

from tests.support import build_app, cli_env, init_identity, run_cli


class HeaderMailbox:
    def __init__(self) -> None:
        self.items = [
            {
                "id": "provider/id@example",
                "from": "sender@example.com",
                "to": "bot@example.com",
                "subject": "new task",
                "status": "new",
                "body": "MUST-NOT-LEAK-FROM-LIST",
            },
            {
                "id": "seen-id",
                "from": "sender@example.com",
                "to": "bot@example.com",
                "subject": "old task",
                "status": "seen",
                "body": "MUST-NOT-LEAK-FROM-LIST",
            },
        ]

    def list(self, identity_id, *, credential=None, address=None):
        del identity_id, credential, address
        return [dict(item) for item in self.items]

    def receive(
        self,
        identity_id,
        *,
        credential=None,
        address=None,
        message_id=None,
        include_body=True,
    ):
        del identity_id, credential, address, message_id, include_body
        return [dict(self.items[0])]


class HeaderFactory:
    def __init__(self, mailbox: HeaderMailbox) -> None:
        self.mailbox = mailbox
        self.bindings: list[str] = []

    def for_binding(self, binding: str) -> HeaderMailbox:
        self.bindings.append(binding)
        return self.mailbox


def _ready_app(vault: Path, monkeypatch):
    app = build_app(vault, email_backend="agentmail")
    init_identity(app, monkeypatch)
    app.client.create("email.address", "bot@example.com")
    app.client.create("email.credential", "credential")
    mailbox = HeaderMailbox()
    factory = HeaderFactory(mailbox)
    app.manager._mailboxes = factory
    return app, factory


def test_mark_unmark_filters_and_headers_only(vault, monkeypatch):
    app, _factory = _ready_app(vault, monkeypatch)

    listed = app.client.email_list()
    assert [item["acted"] for item in listed] == [False, False]
    assert all("body" not in item for item in listed)

    assert app.client.email_mark("provider/id@example", acted=True) is True
    assert [item["id"] for item in app.client.email_list(acted=True)] == [
        "provider/id@example"
    ]
    assert [item["id"] for item in app.client.email_list(acted=False)] == ["seen-id"]
    assert [item["id"] for item in app.client.email_list(status="new")] == [
        "provider/id@example"
    ]
    assert app.client.email_list(status="seen", acted=True) == []

    received = app.client.email_receive()
    assert received[0]["acted"] is True
    assert received[0]["body"] == "MUST-NOT-LEAK-FROM-LIST"

    assert app.client.email_mark("provider/id@example", acted=False) is False
    assert all(item["acted"] is False for item in app.client.email_list())


def test_acted_state_is_backend_neutral_private_and_path_safe(vault, monkeypatch):
    app, factory = _ready_app(vault, monkeypatch)
    message_id = "provider/id@example"
    app.client.email_list()
    app.client.email_mark(message_id, acted=True)

    digest = hashlib.sha256(message_id.encode()).hexdigest()
    marker = identity_home(vault, "P") / "email" / "acted" / digest
    assert marker.read_text(encoding="utf-8") == message_id
    assert "/" not in marker.name and "@" not in marker.name
    if os.name != "nt":
        assert marker.stat().st_mode & 0o777 == 0o600
        assert marker.parent.stat().st_mode & 0o777 == 0o700

    assert app.client.email_list()[0]["acted"] is True
    app.manager._email_backend = "imap"
    assert app.client.email_list()[0]["acted"] is True
    assert factory.bindings[-2:] == ["agentmail", "imap"]


def test_legacy_identity_without_acted_tree_defaults_unacted(vault, monkeypatch):
    app, _factory = _ready_app(vault, monkeypatch)
    acted_dir = identity_home(vault, "P") / "email" / "acted"
    assert not acted_dir.exists()
    assert all(item["acted"] is False for item in app.client.email_list())


def test_cli_mark_json_and_backup_restore_marker(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    initialized = run_cli(["--json", "init"], env)
    assert initialized.returncode == 0, initialized.stderr
    MailRefState(vault).remember("agent", "provider/id@example")

    marked = run_cli(["--json", "email", "mark", "provider/id@example", "acted"], env)
    assert marked.returncode == 0, marked.stderr
    assert json.loads(marked.stdout) == {
        "ok": True,
        "id": "provider/id@example",
        "acted": True,
    }
    digest = hashlib.sha256(b"provider/id@example").hexdigest()
    marker = identity_home(vault, "agent") / "email" / "acted" / digest
    assert marker.is_file()

    backup = tmp_path / "backup"
    copied = run_cli(["backup", str(backup)], env)
    assert copied.returncode == 0, copied.stderr
    unmarked = run_cli(
        ["--json", "email", "mark", "provider/id@example", "unacted"], env
    )
    assert json.loads(unmarked.stdout)["acted"] is False
    assert not marker.exists()

    restored = run_cli(["restore", "--force", str(backup)], env)
    assert restored.returncode == 0, restored.stderr
    assert marker.read_text(encoding="utf-8") == "provider/id@example"


def test_cli_list_acted_flags_are_mutually_exclusive(tmp_path):
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["email", "list", "--acted", "--unacted"], env)
    assert proc.returncode == 2
    assert proc.stderr == ""
    assert "not allowed with argument" in json.loads(proc.stdout)["reason"]

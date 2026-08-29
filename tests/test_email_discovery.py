from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentself.cli.commands.email import find_email
from agentself.cli.outcomes import CliSuccess
from agentself.internal.custody.errors import Refused
from agentself.internal.files import identity_home
from agentself.internal.mail_state import (
    MAIL_REF_PATTERN,
    MailRefCollision,
    MailRefState,
    is_mail_ref,
)

from tests.support import build_app, cli_env, init_identity, run_cli


class HeaderMailbox:
    def __init__(self) -> None:
        self.list_calls = 0
        self.received_ids: list[str | None] = []
        self.items = [
            {
                "id": "provider/alpha@example",
                "from": "Alice <ALICE@example.com>",
                "to": "bot@example.com",
                "subject": "Quarterly Invoice",
                "status": "new",
                "body": "BODY-CANARY",
            },
            {
                "id": "provider-beta",
                "from": "bob@example.com",
                "to": "Finance@example.com",
                "subject": "old report",
                "status": "seen",
                "body": "BODY-CANARY",
            },
        ]

    def list(self, identity_id, *, credential=None, address=None):
        del identity_id, credential, address
        self.list_calls += 1
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
        del identity_id, credential, address
        self.received_ids.append(message_id)
        found = [
            dict(item)
            for item in self.items
            if message_id is None or item["id"] == message_id
        ]
        if not include_body:
            for item in found:
                item.pop("body", None)
        return found[:1] if message_id else found


class HeaderFactory:
    def __init__(self, mailbox: HeaderMailbox) -> None:
        self.mailbox = mailbox
        self.bindings: list[str] = []

    def for_binding(self, binding: str) -> HeaderMailbox:
        self.bindings.append(binding)
        return self.mailbox


def _ready_app(vault, monkeypatch):
    app = build_app(vault, email_backend="agentmail")
    init_identity(app, monkeypatch)
    app.client.create("email.address", "bot@example.com")
    app.client.create("email.credential", "credential")
    mailbox = HeaderMailbox()
    factory = HeaderFactory(mailbox)
    app.manager._mailboxes = factory
    return app, mailbox, factory


def test_refs_are_compact_stable_private_and_backend_neutral(vault, monkeypatch):
    app, _mailbox, factory = _ready_app(vault, monkeypatch)
    first = app.client.email_list()
    second = app.client.email_list()

    expected = "m1"
    assert first[0]["ref"] == expected
    assert first[1]["ref"] == "m2"
    assert second[0]["ref"] == expected
    assert is_mail_ref(expected)
    assert first[0]["id"] == "provider/alpha@example"
    assert all("body" not in item for item in first)

    path = identity_home(vault, "P") / "email" / "refs" / expected
    assert path.read_text(encoding="utf-8") == "provider/alpha@example"
    assert "/" not in path.name and "@" not in path.name
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700

    app.manager._email_backend = "imap"
    assert app.client.email_list()[0]["ref"] == expected
    assert factory.bindings[-1] == "imap"


def test_ref_collision_is_detected_without_remapping(vault):
    refs = MailRefState(vault)
    ref = refs.remember("P", "first-provider-id")
    duplicate = identity_home(vault, "P") / "email" / "refs" / "m2"
    duplicate.write_text("first-provider-id", encoding="utf-8")
    with pytest.raises(MailRefCollision):
        refs.remember("P", "first-provider-id")
    assert refs.resolve("P", ref) == "first-provider-id"


def test_ref_state_refuses_linked_storage_directory(tmp_path):
    refs = MailRefState(tmp_path)
    email = identity_home(tmp_path, "P") / "email"
    email.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (email / "refs").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(OSError, match="unsafe mail state directory"):
        refs.remember("P", "provider-id")
    assert list(outside.iterdir()) == []


def test_receive_and_mark_resolve_refs_and_keep_raw_id_compatibility(
    vault, monkeypatch
):
    app, mailbox, _factory = _ready_app(vault, monkeypatch)
    listed = app.client.email_list()
    ref = str(listed[0]["ref"])

    received = app.client.email_receive(message_id=ref, include_body=False)
    assert mailbox.received_ids[-1] == "provider/alpha@example"
    assert received[0]["ref"] == ref
    assert "body" not in received[0]

    assert app.client.email_mark(ref, acted=True) is True
    assert app.client.email_list(acted=True)[0]["id"] == "provider/alpha@example"
    assert app.client.email_mark("provider/alpha@example", acted=False) is False
    raw = app.client.email_receive(message_id="provider-beta")
    assert raw[0]["id"] == "provider-beta"
    assert mailbox.received_ids[-1] == "provider-beta"


def test_unknown_ref_is_refused_before_backend(vault, monkeypatch):
    app, mailbox, _factory = _ready_app(vault, monkeypatch)
    unknown = "m999"
    with pytest.raises(Refused, match="unknown mail ref"):
        app.client.email_receive(message_id=unknown)
    with pytest.raises(Refused, match="unknown mail ref"):
        app.client.email_mark(unknown, acted=True)
    with pytest.raises(Refused, match="unknown mail ref"):
        app.client.email_mark("nosuchref", acted=True)
    with pytest.raises(Refused, match="unknown mail ref"):
        app.client.email_mark("m0", acted=True)
    assert mailbox.received_ids == []


def test_find_is_header_only_case_insensitive_and_applies_filters(vault, monkeypatch):
    app, mailbox, _factory = _ready_app(vault, monkeypatch)
    app.client.email_list()
    app.client.email_mark("provider/alpha@example", acted=True)

    assert [item["id"] for item in app.client.email_find("invoice")] == [
        "provider/alpha@example"
    ]
    assert [item["id"] for item in app.client.email_find("ALICE@EXAMPLE.COM")] == [
        "provider/alpha@example"
    ]
    assert [item["id"] for item in app.client.email_find("finance", status="seen")] == [
        "provider-beta"
    ]
    assert app.client.email_find("invoice", acted=False) == []
    assert all("body" not in item for item in app.client.email_find("report"))
    assert mailbox.list_calls == 6

    with pytest.raises(Refused):
        app.client.email_find("")
    with pytest.raises(Refused):
        app.client.email_find(" ")


def test_ref_mapping_survives_backup_restore(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    initialized = run_cli(["--json", "init"], env)
    assert initialized.returncode == 0, initialized.stderr
    refs = MailRefState(vault)
    ref = refs.remember("agent", "provider/id")
    marked = run_cli(
        ["--json", "email", "mark", "provider/id", "acted"],
        env,
    )
    assert marked.returncode == 0, marked.stderr
    backup = tmp_path / "backup"
    copied = run_cli(["backup", str(backup)], env)
    assert copied.returncode == 0, copied.stderr
    ref_path = identity_home(vault, "agent") / "email" / "refs" / ref
    ref_path.unlink()
    restored = run_cli(["restore", "--force", str(backup)], env)
    assert restored.returncode == 0, restored.stderr
    assert MailRefState(vault).resolve("agent", ref) == "provider/id"


def test_legacy_identity_creates_ref_state_on_first_surface(vault):
    legacy = MailRefState(vault)
    other = legacy.remember("legacy", "first-id")
    assert legacy.resolve("legacy", other) == "first-id"
    assert legacy.known_provider_id("legacy", "first-id") is True
    assert legacy.known_provider_id("legacy", "missing-id") is False
    assert legacy.known_provider_id("legacy", "m1") is False


def test_cli_find_help_ref_syntax_and_unknown_ref_json(tmp_path):
    env = cli_env(tmp_path / "vault")
    initialized = run_cli(["--json", "init"], env)
    assert initialized.returncode == 0, initialized.stderr

    help_result = run_cli(["email", "find", "--help"], env)
    assert help_result.returncode == 0
    assert "never fetches or searches message bodies" in help_result.stdout
    assert "--status" in help_result.stdout
    assert "--acted" in help_result.stdout

    unknown = "m999"
    refused = run_cli(["--json", "email", "mark", unknown, "acted"], env)
    assert refused.returncode == 2
    payload = json.loads(refused.stdout)
    assert payload["error"] == "refused"
    assert payload["reason"] == "unknown mail ref"
    assert payload["next"] == "agentself email list"
    ghost = run_cli(["--json", "email", "mark", "nosuchref", "acted"], env)
    assert ghost.returncode == 2
    assert json.loads(ghost.stdout)["reason"] == "unknown mail ref"
    leading = run_cli(["--json", "email", "mark", "m0", "acted"], env)
    assert leading.returncode == 2
    assert json.loads(leading.stdout)["reason"] == "unknown mail ref"
    received = run_cli(["--json", "email", "receive", unknown], env)
    assert received.returncode == 2
    received_payload = json.loads(received.stdout)
    assert received_payload["error"] == "refused"
    assert received_payload["reason"] == "unknown mail ref"
    assert received_payload["next"] == "agentself email list"
    human = run_cli(["email", "mark", unknown, "acted"], env)
    assert human.returncode == 2
    assert human.stderr == ""
    assert json.loads(human.stdout)["next"] == "agentself email list"
    human_receive = run_cli(["email", "receive", unknown], env)
    assert human_receive.returncode == 2
    assert human_receive.stderr == ""
    assert json.loads(human_receive.stdout)["next"] == "agentself email list"
    blank = run_cli(["--json", "email", "find", " "], env)
    assert blank.returncode == 2
    blank_payload = json.loads(blank.stdout)
    assert blank_payload["error"] == "refused"
    assert blank_payload["next"] == "agentself backends email"
    assert MAIL_REF_PATTERN == r"m[1-9][0-9]{0,11}"


def test_find_json_emits_header_objects_with_ref(monkeypatch):
    ref = "m3"

    class Client:
        def email_find(self, query, *, status=None, acted=None, rejected=None):
            assert (query, status, acted, rejected) == (
                "invoice",
                "new",
                False,
                False,
            )
            return [
                {
                    "id": "provider/id",
                    "ref": ref,
                    "from": "a@example.com",
                    "to": "bot@example.com",
                    "subject": "Invoice",
                    "status": "new",
                    "acted": False,
                }
            ]

    args = SimpleNamespace(
        email_command="find",
        query="invoice",
        status="new",
        acted_filter=False,
        as_json=True,
    )
    monkeypatch.setattr("agentself.cli.commands.email.client", lambda _vault: Client())
    outcome = find_email(args, Path("."))
    assert isinstance(outcome, CliSuccess)
    assert outcome.payload["messages"][0]["ref"] == ref
    assert "body" not in outcome.payload["messages"][0]

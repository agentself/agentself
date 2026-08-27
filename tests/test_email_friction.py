"""No-ref receive, setup status pairs, and safe signup failure categories."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from agentself.backends.email.agentmail import AgentMailMailboxAccess
from agentself.cli.app import main
from agentself.internal.files import identity_home, secrets_home
from agentself.internal.log import MemoryLog
from agentself.internal.names import EMAIL_CONTINUATION_NAME
from agentself.internal.setup import SETUP_ACTION_REQUIRED, SETUP_INPUT_REQUIRED

from tests.support import apply_cli_env, cli_env, run_cli, value_file
from tests.synthetic_email import SyntheticEmailAccess
from tests.test_agentmail_mailbox import API, INBOXES, OURS, SIGN_UP, Http
from tests.test_email_setup_protocol import ScriptedMailbox, _connect, _patch_mailbox
from tests.test_imap_mailbox import ADDRESS, CANARY, FakeImap, FakeSmtp, _raw
from tests.test_imap_mailbox import _box as imap_box

TOKEN = "am_test_token_do_not_leak"
HUMAN = "owner@example.com"


def _patch_agentmail(monkeypatch, http: Http) -> None:
    def _make(self, binding):
        return AgentMailMailboxAccess(
            self._root,
            self._log,
            domain=self._domain,
            poster=http.poster,
            getter=http.getter,
        )

    monkeypatch.setattr(
        "agentself.compose.MailboxAccessFactory.for_binding",
        _make,
    )


def _patch_imap(monkeypatch, mailbox) -> None:
    monkeypatch.setattr(
        "agentself.compose.MailboxAccessFactory.for_binding",
        lambda self, binding: mailbox,
    )


def test_agentmail_no_ref_receive_is_repeatable_header_check(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["init"], env).returncode == 0
    assert (
        run_cli(
            [
                "secret",
                "create",
                "email.credential",
                "--file",
                value_file(tmp_path, TOKEN),
            ],
            env,
        ).returncode
        == 0
    )
    inbox_id = "inb_headers"
    message_id = "msg@unsafe"
    http = Http()
    http.on_get(INBOXES, 200, {"inboxes": [{"inbox_id": inbox_id, "email": OURS}]})
    listed = {
        "messages": [
            {
                "message_id": message_id,
                "from": "a@example.com",
                "to": [OURS],
                "subject": "hello",
                "preview": "short",
            }
        ]
    }
    http.on_get(f"{API}/v0/inboxes/{inbox_id}/messages", 200, listed)
    quoted = quote(message_id, safe="")
    http.on_get(
        f"{API}/v0/inboxes/{inbox_id}/messages/{quoted}",
        200,
        {"text": "full body"},
    )
    _patch_agentmail(monkeypatch, http)
    apply_cli_env(monkeypatch, env)
    first_code = main(["email", "receive"])
    first = json.loads(capsys.readouterr().out)
    second_code = main(["email", "receive"])
    second = json.loads(capsys.readouterr().out)
    assert first_code == second_code == 0
    assert first["messages"]
    assert first["messages"] == second["messages"]
    assert all("body" not in item for item in first["messages"])
    assert first["messages"][0]["status"] == "new"
    seen = identity_home(vault, "agent") / "agentmail" / "seen"
    assert not seen.exists() or list(seen.iterdir()) == []
    body_gets = [
        url for url, _headers in http.gets if url.endswith(f"/messages/{quoted}")
    ]
    assert body_gets == []
    ref = first["messages"][0]["ref"]
    body_file = tmp_path / "body.txt"
    fetched = main(["email", "receive", ref, "--file", str(body_file)])
    consumed = json.loads(capsys.readouterr().out)
    assert fetched == 0
    assert consumed["messages"][0]["status"] == "seen"
    assert body_file.read_text(encoding="utf-8") == "full body"
    assert seen.is_dir()
    assert list(seen.iterdir())
    assert any(url.endswith(f"/messages/{quoted}") for url, _headers in http.gets)
    listed_after = main(["email", "list"])
    after = json.loads(capsys.readouterr().out)
    assert listed_after == 0
    assert after["messages"][0]["status"] == "seen"


def test_imap_no_ref_receive_does_not_mark_seen(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["init", "--email", "imap"], env).returncode == 0
    imap = FakeImap(
        [
            {
                "uid": "11",
                "raw": _raw(subject="inbox-subject", body="inbox-body", uid="11"),
                "seen": False,
            }
        ]
    )
    mailbox = imap_box(vault, MemoryLog(), imap, FakeSmtp())
    _patch_imap(monkeypatch, mailbox)
    apply_cli_env(monkeypatch, env)
    monkeypatch.setenv("AGENTSELF_EMAIL_ADDRESS", ADDRESS)
    monkeypatch.setenv("AGENTSELF_MAIL_PASSWORD", CANARY)
    first_code = main(["email", "receive"])
    first = json.loads(capsys.readouterr().out)
    second_code = main(["email", "receive"])
    second = json.loads(capsys.readouterr().out)
    assert first_code == second_code == 0
    assert first["messages"] == second["messages"]
    assert first["messages"][0]["status"] == "new"
    assert imap.messages[0]["seen"] is False
    assert all("body" not in item for item in first["messages"])
    ref = first["messages"][0]["ref"]
    fetched = main(["email", "receive", ref])
    consumed = json.loads(capsys.readouterr().out)
    assert fetched == 0
    assert consumed["messages"][0]["status"] == "seen"
    assert imap.messages[0]["seen"] is True


def test_synthetic_no_ref_receive_uses_list_path(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["init"], env).returncode == 0
    SyntheticEmailAccess.reset()
    SyntheticEmailAccess.inbox = [
        {
            "id": "msg-1",
            "from": "a@example.com",
            "to": "agent@example.com",
            "subject": "hello",
            "body": "secret-body",
            "status": "new",
        }
    ]
    _patch_mailbox(monkeypatch, SyntheticEmailAccess())
    apply_cli_env(monkeypatch, env)
    first_code = main(["email", "receive"])
    first = json.loads(capsys.readouterr().out)
    second_code = main(["email", "receive"])
    second = json.loads(capsys.readouterr().out)
    assert first_code == second_code == 0
    assert first["messages"] == second["messages"]
    assert first["messages"][0]["subject"] == "hello"
    assert all("body" not in item for item in first["messages"])
    assert SyntheticEmailAccess.listed == 2
    assert SyntheticEmailAccess.received == 0
    ref = first["messages"][0]["ref"]
    fetched = main(["email", "receive", ref])
    consumed = json.loads(capsys.readouterr().out)
    assert fetched == 0
    assert consumed["messages"][0]["id"] == "msg-1"
    assert SyntheticEmailAccess.received == 1
    assert "secret-body" not in first["messages"][0]
    assert "secret-body" not in json.dumps(first) + json.dumps(second)


def test_setup_menus_match_status_and_human_action(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["init"], env).returncode == 0
    http = Http()
    _patch_agentmail(monkeypatch, http)
    apply_cli_env(monkeypatch, env)
    code = main(["email", "connect"])
    menu = json.loads(capsys.readouterr().out)
    assert code == 3
    assert menu["status"] == SETUP_INPUT_REQUIRED
    assert menu["human_action_required"] is False
    assert menu["option"]["name"] == "setup_method"
    method = value_file(tmp_path, "existing_credential", "method.txt")
    code = main(
        [
            "email",
            "connect",
            "--continue",
            "--state",
            menu["state"],
            "--result-file",
            method,
        ]
    )
    cred = json.loads(capsys.readouterr().out)
    assert code == 3
    assert cred["status"] == SETUP_ACTION_REQUIRED
    assert cred["human_action_required"] is True
    assert cred["option"]["name"] == "credential"
    assert cred["option"]["action"]["kind"] == "open_url"

    create = value_file(tmp_path, "create_account", "create.txt")
    code = main(
        [
            "email",
            "connect",
            "--continue",
            "--state",
            menu["state"],
            "--result-file",
            create,
        ]
    )
    human = json.loads(capsys.readouterr().out)
    assert code == 3
    assert human["status"] == SETUP_INPUT_REQUIRED
    assert human["human_action_required"] is False
    assert human["option"]["name"] == "human_email"


def test_imap_address_and_credential_are_input_required(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["init", "--email", "imap"], env).returncode == 0
    _patch_imap(
        monkeypatch, imap_box(tmp_path / "vault", MemoryLog(), FakeImap(), FakeSmtp())
    )
    apply_cli_env(monkeypatch, env)
    monkeypatch.setenv("AGENTSELF_EMAIL_BACKEND", "imap")
    code = main(["email", "connect"])
    first = json.loads(capsys.readouterr().out)
    assert code == 3
    assert first["status"] == SETUP_INPUT_REQUIRED
    assert first["human_action_required"] is False
    assert first["option"]["name"] == "address"
    addr = value_file(tmp_path, ADDRESS, "address.txt")
    code = main(
        [
            "email",
            "connect",
            "--continue",
            "--state",
            first["state"],
            "--result-file",
            addr,
        ]
    )
    cred = json.loads(capsys.readouterr().out)
    assert code == 3
    assert cred["status"] == SETUP_INPUT_REQUIRED
    assert cred["human_action_required"] is False
    assert cred["option"]["name"] == "credential"


def test_backend_human_action_mismatch_is_derived(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["init"], env).returncode == 0

    def connect(_token, _address, _answers):
        from agentself.backends.email.contract import setup_needed
        from agentself.internal.setup import credential_option

        return setup_needed(
            credential_option(),
            status=SETUP_INPUT_REQUIRED,
            human_action_required=True,
        )

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    code, payload = _connect(monkeypatch, capsys, env)
    assert code == 3
    assert payload["status"] == SETUP_INPUT_REQUIRED
    assert payload["human_action_required"] is False


def test_signup_failure_categories_recover_to_connect(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["init"], env).returncode == 0
    cases = (
        (409, "setup_conflict", False),
        (403, "setup_forbidden", False),
        (422, "setup_rejected", False),
        (404, "backend_unavailable", True),
        (503, "backend_unavailable", True),
    )
    for status, reason, retryable in cases:
        http = Http()
        http.on_post(SIGN_UP, status, {"error": "unavailable"})
        _patch_agentmail(monkeypatch, http)
        apply_cli_env(monkeypatch, env)
        menu_code = main(["email", "connect"])
        menu = json.loads(capsys.readouterr().out)
        assert menu_code == 3
        create = value_file(tmp_path, "create_account", f"create-{status}.txt")
        email_code = main(
            [
                "email",
                "connect",
                "--continue",
                "--state",
                menu["state"],
                "--result-file",
                create,
            ]
        )
        human = json.loads(capsys.readouterr().out)
        assert email_code == 3
        human_file = value_file(tmp_path, HUMAN, f"human-{status}.txt")
        failed_code = main(
            [
                "email",
                "connect",
                "--continue",
                "--state",
                human["state"],
                "--result-file",
                human_file,
            ]
        )
        failed = json.loads(capsys.readouterr().out)
        assert failed_code == 1
        assert failed["reason"] == reason
        assert failed["retryable"] is retryable
        assert failed["next"] == "agentself email connect"
        assert failed["option"]["name"] == "setup_method"
        assert "existing_credential" in failed["message"]
        assert "alias" not in json.dumps(failed).lower()
        assert not (
            secrets_home(vault, "agent") / f"{EMAIL_CONTINUATION_NAME}.sops"
        ).is_file()
        assert len([post for post in http.posts if post[0] == SIGN_UP]) == 1
        assert all("username" in json.loads(post[2]) for post in http.posts)
        usernames = {json.loads(post[2])["username"] for post in http.posts}
        assert len(usernames) == 1


def test_signup_transport_error_is_backend_unavailable(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["init"], env).returncode == 0
    http = Http()
    http.post_raises(TimeoutError("timeout"))
    _patch_agentmail(monkeypatch, http)
    apply_cli_env(monkeypatch, env)
    assert main(["email", "connect"]) == 3
    menu = json.loads(capsys.readouterr().out)
    create = value_file(tmp_path, "create_account", "create.txt")
    assert (
        main(
            [
                "email",
                "connect",
                "--continue",
                "--state",
                menu["state"],
                "--result-file",
                create,
            ]
        )
        == 3
    )
    human_step = json.loads(capsys.readouterr().out)
    human = value_file(tmp_path, HUMAN, "human.txt")
    code = main(
        [
            "email",
            "connect",
            "--continue",
            "--state",
            human_step["state"],
            "--result-file",
            human,
        ]
    )
    failed = json.loads(capsys.readouterr().out)
    assert code == 1
    assert failed["reason"] == "backend_unavailable"
    assert failed["retryable"] is True
    assert failed["next"] == "agentself email connect"
    assert failed["option"]["name"] == "setup_method"

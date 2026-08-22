"""Generic email setup: resumable, encrypted, provider-neutral."""

from __future__ import annotations

import json
import time
from pathlib import Path

from agentself.backends.email.contract import (
    MailboxAccess,
    mailbox_view,
    setup_needed,
)
from agentself.cli.app import main
from agentself.internal.setup import (
    SETUP_ACTION_REQUIRED,
    SETUP_PENDING,
    address_option,
    credential_option,
)

from tests.support import apply_cli_env, cli_env, run_cli

ADDRESS = "agent@example.com"
CREDENTIAL = "app-password-do-not-leak"


class ScriptedMailbox(MailboxAccess):
    """Test double. A new backend needs this contract, not parser or Gateway changes."""

    def __init__(self, connect_fn) -> None:
        self._connect = connect_fn

    def send(self, principal_id, to, subject, body, send_token=None, address=None):
        del principal_id, to, subject, body, send_token, address

    def recv(self, principal_id, *, send_token=None, address=None, message_id=None):
        del principal_id, send_token, address, message_id
        return []

    def list(self, principal_id, *, send_token=None, address=None):
        del principal_id, send_token, address
        return []

    def describe(self, principal_id, *, send_token=None, address=None):
        del principal_id, send_token, address
        return mailbox_view()

    def connect(self, principal_id, *, send_token=None, address=None, answers=None):
        del principal_id
        extra = dict(answers or {})
        return self._connect(send_token, address, extra)


def _patch_mailbox(monkeypatch, mailbox: ScriptedMailbox) -> None:
    monkeypatch.setattr(
        "agentself.compose.MailboxAccessFactory.for_binding",
        lambda self, binding: mailbox,
    )


def _connect(
    monkeypatch, capsys, env: dict[str, str], extra: list[str] | None = None
) -> tuple[int, dict]:
    apply_cli_env(monkeypatch, env)
    for key in ("AGENTSELF_EMAIL_ADDRESS", "AGENTSELF_EMAIL_CREDENTIAL"):
        if key in env:
            monkeypatch.setenv(key, env[key])
    code = main(["--json", "email", "connect", *(extra or [])])
    captured = capsys.readouterr()
    assert captured.err == "", captured.err
    return code, json.loads(captured.out)


def test_new_backend_uses_existing_connect_without_parser_changes(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def connect(token, address, answers):
        wanted = (address or answers.get("address") or "").strip()
        secret = (token or answers.get("credential") or "").strip()
        if not secret:
            return setup_needed([credential_option()])
        if not wanted:
            return setup_needed([address_option(required=True)])
        return mailbox_view(wanted, owned_address=True)

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    code, first = _connect(monkeypatch, capsys, env)
    assert code == 3
    assert first["status"] == "input_required"
    assert first["setup_id"]
    assert first["continue"].startswith("agentself email connect --continue ")
    assert "agentmail" not in json.dumps(first).lower()
    assert [item["name"] for item in first["options"]] == ["credential"]
    assert first["options"][0]["sensitive"] is True
    listed = json.loads(run_cli(["--json", "secret", "list"], env).stdout)
    assert all(not name.startswith("internal.") for name in listed["names"])

    cred = tmp_path / "credential.txt"
    cred.write_text(CREDENTIAL, encoding="utf-8")
    code, second = _connect(
        monkeypatch,
        capsys,
        env,
        ["--continue", first["setup_id"], "--result-file", str(cred)],
    )
    assert code == 3
    assert second["setup_id"] == first["setup_id"]
    assert [item["name"] for item in second["options"]] == ["address"]

    addr = tmp_path / "address.txt"
    addr.write_text(ADDRESS, encoding="utf-8")
    code, done = _connect(
        monkeypatch,
        capsys,
        env,
        ["--continue", second["setup_id"], "--result-file", str(addr)],
    )
    assert code == 0
    assert done["ok"] is True
    assert done["status"] == "connected"
    assert done["address"] == ADDRESS
    shown = json.loads(run_cli(["--json", "email", "show"], env).stdout)
    assert shown["ok"] is True
    assert shown["address"] == ADDRESS
    leftover = json.loads(run_cli(["--json", "secret", "list"], env).stdout)
    assert all(not name.startswith("internal.") for name in leftover["names"])
    assert CREDENTIAL not in json.dumps(first) + json.dumps(second) + json.dumps(done)


def test_human_action_and_pending_states(tmp_path: Path, monkeypatch, capsys) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def action(_token, _address, _answers):
        return setup_needed(
            [],
            status=SETUP_ACTION_REQUIRED,
            human_action_required=True,
            message="Confirm this identity in the mail provider",
        )

    _patch_mailbox(monkeypatch, ScriptedMailbox(action))
    code, payload = _connect(monkeypatch, capsys, env)
    assert code == 3
    assert payload["status"] == "action_required"
    assert payload["human_action_required"] is True
    assert payload["setup_id"]
    blob = json.dumps(payload).lower()
    assert "oauth" not in blob
    assert "otp" not in blob

    def pending(_token, _address, _answers):
        return setup_needed([], status=SETUP_PENDING, message="Provisioning")

    _patch_mailbox(monkeypatch, ScriptedMailbox(pending))
    code, waiting = _connect(monkeypatch, capsys, env)
    assert code == 3
    assert waiting["status"] == "pending"
    assert waiting["human_action_required"] is False


def test_expired_and_unknown_setup_are_failed(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def connect(_token, _address, _answers):
        return setup_needed([credential_option()], expires_at=time.time() - 5)

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    code, first = _connect(monkeypatch, capsys, env)
    assert code == 3
    cred = tmp_path / "credential.txt"
    cred.write_text(CREDENTIAL, encoding="utf-8")
    code, expired = _connect(
        monkeypatch,
        capsys,
        env,
        ["--continue", first["setup_id"], "--result-file", str(cred)],
    )
    assert code == 1
    assert expired["ok"] is False
    assert expired["status"] == "failed"
    code, unknown = _connect(
        monkeypatch,
        capsys,
        env,
        [
            "--continue",
            "deadbeefdeadbeefdeadbeefdeadbeef",
            "--result-file",
            str(cred),
        ],
    )
    assert code == 1
    assert unknown["status"] == "failed"


def test_import_env_persists_credentials_ordinary_connect_does_not(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def connect(token, address, answers):
        wanted = (address or answers.get("address") or "").strip()
        secret = (token or answers.get("credential") or "").strip()
        if not secret or not wanted:
            return setup_needed([address_option(required=True), credential_option()])
        return mailbox_view(wanted, owned_address=True)

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    env["AGENTSELF_EMAIL_ADDRESS"] = ADDRESS
    env["AGENTSELF_EMAIL_CREDENTIAL"] = CREDENTIAL
    code, ordinary = _connect(monkeypatch, capsys, env)
    assert code == 0, ordinary
    assert ordinary["status"] == "connected"
    missing = run_cli(["--json", "secret", "exists", "email.send.token"], env)
    assert missing.returncode == 3
    assert json.loads(missing.stdout)["exists"] is False

    other = tmp_path / "imported"
    other.mkdir()
    imported_env = cli_env(other)
    assert run_cli(["--json", "init"], imported_env).returncode == 0
    imported_env["AGENTSELF_EMAIL_ADDRESS"] = ADDRESS
    imported_env["AGENTSELF_EMAIL_CREDENTIAL"] = CREDENTIAL
    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    code, imported = _connect(monkeypatch, capsys, imported_env, ["--import-env"])
    assert code == 0, imported
    present = run_cli(["--json", "secret", "exists", "email.send.token"], imported_env)
    assert present.returncode == 0
    assert json.loads(present.stdout)["exists"] is True
    leaked = run_cli(["--json", "secret", "get", "email.send.token"], imported_env)
    assert json.loads(leaked.stdout)["value"] == CREDENTIAL

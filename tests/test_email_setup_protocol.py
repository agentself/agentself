"""Generic email setup: resumable, one option at a time, provider-neutral."""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

from agentself.backends.email.contract import (
    MailboxAccess,
    MailboxError,
    mailbox_view,
    setup_needed,
)
from agentself.cli.app import _prompt_setup_option, main
from agentself.cli.parser import _parser
from agentself.client import Gateway
from agentself.internal.custody.manager import CustodyManager
from agentself.internal.setup import (
    SETUP_ACTION_REQUIRED,
    SETUP_PENDING,
    address_option,
    credential_option,
)

from tests.support import apply_cli_env, cli_env, run_cli, value_file

ADDRESS = "agent@example.com"
CREDENTIAL = "app-password-do-not-leak"


class ScriptedMailbox(MailboxAccess):
    """Test double. A new backend needs this contract, not parser or Gateway changes."""

    def __init__(self, connect_fn) -> None:
        self._connect = connect_fn
        self.calls: list[tuple[str, str | None, str | None]] = []

    def send(self, principal_id, to, subject, body, send_token=None, address=None):
        del principal_id, to, subject, body
        self._require_runtime(send_token, address)
        self.calls.append(("send", send_token, address))

    def recv(self, principal_id, *, send_token=None, address=None, message_id=None):
        del principal_id, message_id
        self._require_runtime(send_token, address)
        self.calls.append(("recv", send_token, address))
        return [{"id": "message-1", "subject": "hello"}]

    def list(self, principal_id, *, send_token=None, address=None):
        del principal_id
        self._require_runtime(send_token, address)
        self.calls.append(("list", send_token, address))
        return [{"id": "message-1", "subject": "hello"}]

    def describe(self, principal_id, *, send_token=None, address=None):
        del principal_id
        self.calls.append(("describe", send_token, address))
        if send_token and address:
            return mailbox_view(address, owned_address=True)
        return mailbox_view()

    def connect(self, principal_id, *, send_token=None, address=None, answers=None):
        del principal_id
        extra = dict(answers or {})
        return self._connect(send_token, address, extra)

    @staticmethod
    def _require_runtime(send_token, address) -> None:
        if not send_token or not address:
            raise MailboxError("not ready")


def _patch_mailbox(monkeypatch, mailbox: ScriptedMailbox) -> None:
    monkeypatch.setattr(
        "agentself.compose.MailboxAccessFactory.for_binding",
        lambda self, binding: mailbox,
    )


def _connect(
    monkeypatch, capsys, env: dict[str, str], extra: list[str] | None = None
) -> tuple[int, dict]:
    apply_cli_env(monkeypatch, env)
    for key in (
        "AGENTSELF_EMAIL_ADDRESS",
        "AGENTSELF_EMAIL_CREDENTIAL",
        "AGENTSELF_AGENTMAIL_API_KEY",
    ):
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
            return setup_needed(credential_option())
        if not wanted:
            return setup_needed(address_option(required=True))
        return mailbox_view(wanted, owned_address=True)

    mailbox = ScriptedMailbox(connect)
    _patch_mailbox(monkeypatch, mailbox)
    code, first = _connect(monkeypatch, capsys, env)
    assert code == 3
    assert first["status"] == "input_required"
    assert first["state"]
    assert first["continue"].startswith(
        "agentself --json email connect --continue --state "
    )
    assert "--result-file PATH" in first["continue"]
    assert "agentmail" not in json.dumps(first).lower()
    assert first["option"]["name"] == "credential"
    assert first["option"]["sensitive"] is True
    listed = json.loads(run_cli(["--json", "secret", "list"], env).stdout)
    assert all(not name.startswith("internal.") for name in listed["names"])

    cred = value_file(tmp_path, CREDENTIAL, "credential.txt")
    code, second = _connect(
        monkeypatch,
        capsys,
        env,
        ["--continue", "--state", first["state"], "--result-file", cred],
    )
    assert code == 3
    assert second["state"] != first["state"]
    assert second["option"]["name"] == "address"

    addr = value_file(tmp_path, ADDRESS, "address.txt")
    code, done = _connect(
        monkeypatch,
        capsys,
        env,
        ["--continue", "--state", second["state"], "--result-file", addr],
    )
    assert code == 0
    assert done["ok"] is True
    assert done["status"] == "connected"
    assert done["address"] == ADDRESS
    assert main(["--json", "email", "show"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["ok"] is True
    assert shown["address"] == ADDRESS
    assert main(["email", "send", "to@example.com", "subject", "body"]) == 0
    assert main(["email", "receive"]) == 0
    assert main(["email", "list"]) == 0
    capsys.readouterr()
    assert [call[0] for call in mailbox.calls[-4:]] == [
        "describe",
        "send",
        "recv",
        "list",
    ]
    assert all(call[1:] == (CREDENTIAL, ADDRESS) for call in mailbox.calls[-4:])
    leftover = json.loads(run_cli(["--json", "secret", "list"], env).stdout)
    assert all(not name.startswith("internal.") for name in leftover["names"])
    assert CREDENTIAL not in json.dumps(first) + json.dumps(second) + json.dumps(done)


def test_human_action_and_pending_states(tmp_path: Path, monkeypatch, capsys) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def action(_token, _address, _answers):
        return setup_needed(
            None,
            status=SETUP_ACTION_REQUIRED,
            human_action_required=True,
            message="Confirm this identity in the mail provider",
        )

    _patch_mailbox(monkeypatch, ScriptedMailbox(action))
    code, payload = _connect(monkeypatch, capsys, env)
    assert code == 3
    assert payload["status"] == "action_required"
    assert payload["human_action_required"] is True
    assert payload["state"]
    blob = json.dumps(payload).lower()
    assert "oauth" not in blob
    assert "otp" not in blob

    def pending(_token, _address, _answers):
        return setup_needed(None, status=SETUP_PENDING, message="Provisioning")

    _patch_mailbox(monkeypatch, ScriptedMailbox(pending))
    code, waiting = _connect(monkeypatch, capsys, env)
    assert code == 3
    assert waiting["status"] == "pending"
    assert waiting["human_action_required"] is False


def test_human_renderer_consumes_generic_secret_action_and_choice(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def connect(token, address, answers):
        del answers
        if not token:
            return setup_needed(
                credential_option(
                    prompt="Paste the provider credential",
                    help="Create a credential in the provider console.",
                    action={
                        "kind": "open_url",
                        "label": "Open provider console",
                        "url": "https://provider.example/keys",
                    },
                )
            )
        if not address:
            return setup_needed(
                address_option(
                    required=True,
                    prompt="Choose the inbox for this identity",
                    help="Select an inbox owned by this credential.",
                    choices=["assistant@example.com", "support@example.com"],
                )
            )
        return mailbox_view(address, owned_address=True)

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    apply_cli_env(monkeypatch, env)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        "agentself.cli.app.getpass.getpass",
        lambda _prompt="", **_kwargs: CREDENTIAL,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": "2")

    assert main(["email", "connect"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert "Open provider console:" in output.out
    assert "https://provider.example/keys" in output.out
    assert "Paste the provider credential (input is hidden):" in output.out
    assert "1. assistant@example.com" in output.out
    assert "2. support@example.com" in output.out
    assert "Checking the credential..." in output.out
    assert output.out.endswith(
        "Connected: support@example.com\n"
        "The credential is encrypted in this identity.\n"
    )
    assert CREDENTIAL not in output.out


def test_generic_setup_keeps_public_surfaces_provider_neutral() -> None:
    assert list(inspect.signature(_parser).parameters) == []
    assert list(inspect.signature(Gateway.email_connect).parameters) == [
        "self",
        "hold_owner",
        "answers",
        "state",
    ]
    assert list(inspect.signature(CustodyManager.email_connect).parameters) == [
        "self",
        "caller",
        "hold_owner",
        "answers",
        "state",
    ]
    assert "agentmail" not in inspect.getsource(_prompt_setup_option).lower()


def test_unknown_state_is_failed(tmp_path: Path, monkeypatch, capsys) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def connect(_token, _address, _answers):
        return setup_needed(credential_option())

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    cred = value_file(tmp_path, CREDENTIAL, "credential.txt")
    code, unknown = _connect(
        monkeypatch,
        capsys,
        env,
        [
            "--continue",
            "--state",
            "not-a-state",
            "--result-file",
            cred,
        ],
    )
    assert code == 1
    assert unknown["ok"] is False
    assert unknown["status"] == "failed"


def test_env_connects_without_copying_into_vault(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def connect(token, address, answers):
        wanted = (address or answers.get("address") or "").strip()
        secret = (token or answers.get("credential") or "").strip()
        if not secret or not wanted:
            return setup_needed(address_option(required=True))
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


@pytest.mark.parametrize(
    "credential_env",
    ["AGENTSELF_EMAIL_CREDENTIAL", "AGENTSELF_AGENTMAIL_API_KEY"],
)
def test_runtime_credential_env_sources_cover_all_email_operations(
    tmp_path: Path, monkeypatch, capsys, credential_env: str
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def connect(token, address, answers):
        del answers
        if not token or not address:
            return setup_needed(address_option(required=True))
        return mailbox_view(address, owned_address=True)

    mailbox = ScriptedMailbox(connect)
    _patch_mailbox(monkeypatch, mailbox)
    env["AGENTSELF_EMAIL_ADDRESS"] = ADDRESS
    env[credential_env] = CREDENTIAL
    code, connected = _connect(monkeypatch, capsys, env)
    assert code == 0, connected

    assert main(["email", "show"]) == 0
    assert main(["email", "send", "to@example.com", "subject", "body"]) == 0
    assert main(["email", "receive"]) == 0
    assert main(["email", "list"]) == 0
    captured = capsys.readouterr()
    assert CREDENTIAL not in captured.out + captured.err
    assert [call[0] for call in mailbox.calls[-4:]] == [
        "describe",
        "send",
        "recv",
        "list",
    ]
    assert all(call[1:] == (CREDENTIAL, ADDRESS) for call in mailbox.calls[-4:])

    for key in ("AGENTSELF_EMAIL_ADDRESS", credential_env):
        monkeypatch.delenv(key)
    assert main(["--json", "email", "show"]) == 0
    without_env = json.loads(capsys.readouterr().out)
    assert without_env["ready"] is False
    assert without_env["address"] is None
    assert (
        main(
            [
                "--json",
                "email",
                "send",
                "to@example.com",
                "subject",
                "body",
            ]
        )
        == 1
    )
    failed = json.loads(capsys.readouterr().out)
    assert failed["reason"] == "not_ready"

    names = json.loads(run_cli(["--json", "secret", "list"], env).stdout)["names"]
    assert "email.address" not in names
    assert "email.send.token" not in names

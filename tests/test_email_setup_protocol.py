"""Generic email setup: resumable, one option at a time, provider-neutral."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agentself.backends.email.contract import (
    MailboxAccess,
    MailboxError,
    mailbox_view,
    require_secret,
    setup_needed,
)
from agentself.cli.app import main
from agentself.internal.custody.manager import _channel_from_mailbox
from agentself.internal.files import secrets_home
from agentself.internal.names import (
    EMAIL_ADDRESS_NAME,
    EMAIL_CONTINUATION_NAME,
    EMAIL_CREDENTIAL_NAME,
    WALLET_KEY_NAME,
)
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
    """Test double. A new backend needs this contract, not parser or Client changes."""

    def __init__(self, connect_fn, options=None) -> None:
        self._connect = connect_fn
        self._options = options
        self.calls: list[tuple[str, str | None, str | None]] = []

    def send(self, identity_id, to, subject, body, credential=None, address=None):
        del identity_id, to, subject, body
        self._require_runtime(credential, address)
        self.calls.append(("send", credential, address))

    def receive(
        self,
        identity_id,
        *,
        credential=None,
        address=None,
        message_id=None,
        include_body=True,
    ):
        del identity_id, message_id, include_body
        self._require_runtime(credential, address)
        self.calls.append(("receive", credential, address))
        return [{"id": "message-1", "subject": "hello"}]

    def list(self, identity_id, *, credential=None, address=None):
        del identity_id
        self._require_runtime(credential, address)
        self.calls.append(("list", credential, address))
        return [{"id": "message-1", "subject": "hello"}]

    def describe(self, identity_id, *, credential=None, address=None):
        del identity_id
        self.calls.append(("describe", credential, address))
        if credential and address:
            return mailbox_view(address, owned_address=True)
        return mailbox_view()

    def setup_options(self):
        if self._options is not None:
            return self._options
        return (
            credential_option(persist=True, persist_as=EMAIL_CREDENTIAL_NAME),
            address_option(persist=True, persist_as=EMAIL_ADDRESS_NAME),
        )

    def connect(
        self, identity_id, *, credential=None, address=None, answers=None, state=None
    ):
        del identity_id, state
        extra = dict(answers or {})
        return self._connect(credential, address, extra)

    @staticmethod
    def _require_runtime(credential, address) -> None:
        if not credential or not address:
            raise MailboxError("not ready")


def _patch_mailbox(monkeypatch, mailbox: ScriptedMailbox) -> None:
    monkeypatch.setattr(
        "agentself.compose.MailboxAccessFactory.for_binding",
        lambda self, binding: mailbox,
    )


def _continuation_file(vault: Path, identity_id: str = "agent") -> Path:
    return secrets_home(vault, identity_id) / f"{EMAIL_CONTINUATION_NAME}.sops"


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
        "receive",
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
    assert "Create a credential in the provider console." not in output.out
    assert "1. assistant@example.com" in output.out
    assert "2. support@example.com" in output.out
    assert "Checking the credential..." in output.out
    assert output.out.endswith(
        "Connected: support@example.com\n"
        "The credential is encrypted in this identity.\n"
    )
    assert CREDENTIAL not in output.out


def test_human_renderer_empty_secret_does_not_print_continue(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def connect(token, address, answers):
        del address, answers
        if not token:
            return setup_needed(
                credential_option(
                    prompt="Paste the provider credential",
                    help="AGENT PROCEDURE DO NOT PRINT",
                    action={
                        "kind": "open_url",
                        "label": "Open provider console",
                        "url": "https://provider.example/keys",
                    },
                )
            )
        return mailbox_view("agent@example.com", owned_address=True)

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    apply_cli_env(monkeypatch, env)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        "agentself.cli.app.getpass.getpass",
        lambda _prompt="", **_kwargs: "",
    )
    assert main(["email", "connect"]) == 3
    output = capsys.readouterr()
    assert "AGENT PROCEDURE DO NOT PRINT" not in output.out + output.err
    assert "nothing entered" in output.err
    assert "--continue" not in output.err
    assert "--result-file" not in output.err
    assert "https://provider.example/keys" in output.out


def test_human_renderer_strips_windows_trailing_cr(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def connect(token, address, answers):
        del address
        secret = (token or (answers or {}).get("credential") or "").strip()
        if not secret:
            return setup_needed(credential_option())
        assert "\r" not in secret
        assert secret == CREDENTIAL
        return mailbox_view(ADDRESS, owned_address=True)

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    apply_cli_env(monkeypatch, env)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        "agentself.cli.app.getpass.getpass",
        lambda _prompt="", **_kwargs: CREDENTIAL + "\r",
    )
    assert main(["email", "connect"]) == 0
    output = capsys.readouterr()
    assert f"Connected: {ADDRESS}\n" in output.out
    assert CREDENTIAL not in output.out


def test_human_renderer_channel_failure_is_not_a_traceback(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def connect(token, address, answers):
        del address, answers
        if not token:
            return setup_needed(credential_option())
        raise MailboxError("invalid credentials")

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    apply_cli_env(monkeypatch, env)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        "agentself.cli.app.getpass.getpass",
        lambda _prompt="", **_kwargs: CREDENTIAL,
    )
    assert main(["email", "connect"]) == 1
    output = capsys.readouterr()
    assert "Traceback" not in output.out + output.err
    assert "ChannelFailure" not in output.out + output.err
    assert "error: invalid credentials" in output.err
    assert "next: agentself email connect" in output.err
    assert CREDENTIAL not in output.out + output.err


def test_channel_from_mailbox_local_input_is_not_rpc() -> None:
    paste = MailboxError("credential looks like http header")
    assert _channel_from_mailbox(paste).reason != "rpc"
    connection = MailboxError("bad connection string")
    assert _channel_from_mailbox(connection).reason == "mailbox_error"
    network_word = MailboxError("network id is required")
    assert _channel_from_mailbox(network_word).reason == "mailbox_error"
    timeout_word = MailboxError("timeout waiting for operator")
    assert _channel_from_mailbox(timeout_word).reason == "mailbox_error"
    local = MailboxError("no inbox")
    local.__cause__ = FileNotFoundError("missing")
    assert _channel_from_mailbox(local).reason == "mailbox_error"
    assert _channel_from_mailbox(MailboxError("invalid host")).reason == "invalid host"
    assert _channel_from_mailbox(MailboxError("invalid port")).reason == "invalid port"
    http = MailboxError("http failed")
    assert _channel_from_mailbox(http).reason == "rpc"
    rpc = MailboxError("rpc failed")
    assert _channel_from_mailbox(rpc).reason == "rpc"
    timed = MailboxError("send failed")
    timed.__cause__ = TimeoutError("timeout")
    assert _channel_from_mailbox(timed).reason == "rpc"
    reset = MailboxError("send failed")
    reset.__cause__ = ConnectionResetError("reset")
    assert _channel_from_mailbox(reset).reason == "rpc"
    injected = MailboxError("invalid credentials")
    assert _channel_from_mailbox(injected).reason == "invalid_credential"


def test_human_renderer_control_chars_are_not_rpc(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def connect(token, address, answers):
        del address
        secret = (token or (answers or {}).get("credential") or "").strip()
        if not secret:
            return setup_needed(
                credential_option(
                    prompt="Paste the provider credential",
                    help="AGENT PROCEDURE DO NOT PRINT",
                )
            )
        require_secret(secret)
        return mailbox_view(ADDRESS, owned_address=True)

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    apply_cli_env(monkeypatch, env)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        "agentself.cli.app.getpass.getpass",
        lambda _prompt="", **_kwargs: "tok\r\nAuthorization: Bearer " + CREDENTIAL,
    )
    assert main(["email", "connect"]) == 1
    output = capsys.readouterr()
    blob = output.out + output.err
    assert "Traceback" not in blob
    assert "ChannelFailure" not in blob
    assert "error: rpc" not in blob
    assert "AGENT PROCEDURE DO NOT PRINT" not in blob
    assert "error: invalid credentials" in output.err
    assert CREDENTIAL not in blob


def test_tty_continue_empty_secret_is_nothing_entered(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def connect(token, address, answers):
        del address, answers
        if not token:
            return setup_needed(credential_option(help="AGENT PROCEDURE DO NOT PRINT"))
        return mailbox_view(ADDRESS, owned_address=True)

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    apply_cli_env(monkeypatch, env)
    first = main(["--json", "email", "connect"])
    captured = capsys.readouterr()
    assert first == 3
    state = json.loads(captured.out)["state"]
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        "agentself.cli.app.getpass.getpass",
        lambda _prompt="", **_kwargs: "",
    )
    assert main(["email", "connect", "--continue", "--state", state]) == 3
    output = capsys.readouterr()
    blob = output.out + output.err
    assert "nothing entered" in output.err
    assert "next: agentself email connect" in output.err
    assert "--continue" not in output.err
    assert "--result-file" not in output.err
    assert "--state" not in output.err
    assert "AGENT PROCEDURE DO NOT PRINT" not in blob


@pytest.mark.parametrize(
    ("status", "extra", "reason"),
    [
        (
            SETUP_ACTION_REQUIRED,
            {"human_action_required": True, "message": "Confirm this identity"},
            "human action required",
        ),
        (SETUP_PENDING, {"message": "Provisioning"}, "pending"),
    ],
)
def test_tty_without_option_falls_through_to_setup_pending(
    tmp_path: Path,
    monkeypatch,
    capsys,
    status: str,
    extra: dict[str, object],
    reason: str,
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def connect(_token, _address, _answers):
        return setup_needed(None, status=status, **extra)

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    apply_cli_env(monkeypatch, env)
    first = main(["--json", "email", "connect"])
    captured = capsys.readouterr()
    assert first == 3
    state = json.loads(captured.out)["state"]

    def no_prompt(_prompt="", **_kwargs):
        raise AssertionError("no named option should not prompt")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("agentself.cli.app.getpass.getpass", no_prompt)
    monkeypatch.setattr("builtins.input", no_prompt)

    assert main(["email", "connect", "--continue", "--state", state]) == 3
    output = capsys.readouterr()
    assert "nothing entered" not in output.err
    assert reason in output.err

    assert main(["email", "connect"]) == 3
    output = capsys.readouterr()
    assert "nothing entered" not in output.err
    assert reason in output.err


def test_human_renderer_unexpected_error_is_not_a_traceback(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def connect(token, address, answers):
        del address, answers
        if not token:
            return setup_needed(credential_option())
        raise RuntimeError("ChannelFailure should not leak")

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    apply_cli_env(monkeypatch, env)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        "agentself.cli.app.getpass.getpass",
        lambda _prompt="", **_kwargs: CREDENTIAL,
    )
    assert main(["email", "connect"]) == 1
    output = capsys.readouterr()
    blob = output.out + output.err
    assert "Traceback" not in blob
    assert "ChannelFailure" not in blob
    assert "RuntimeError" not in blob
    assert output.err == "error\n"
    assert CREDENTIAL not in blob


def test_json_continue_control_chars_are_not_rpc(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0

    def connect(token, address, answers):
        del address
        secret = (token or (answers or {}).get("credential") or "").strip()
        if not secret:
            return setup_needed(credential_option(help="AGENT PROCEDURE FOR JSON"))
        require_secret(secret)
        return mailbox_view(ADDRESS, owned_address=True)

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    code, first = _connect(monkeypatch, capsys, env)
    assert code == 3
    assert "AGENT PROCEDURE FOR JSON" in first["option"]["help"]
    cred = tmp_path / "credential.txt"
    cred.write_bytes(b"tok\r\nAuthorization: Bearer paste-artifact")
    code, failed = _connect(
        monkeypatch,
        capsys,
        env,
        [
            "--continue",
            "--state",
            first["state"],
            "--result-file",
            str(cred),
        ],
    )
    assert code == 1
    assert failed["ok"] is False
    assert failed["error"] == "error"
    assert failed["reason"] != "rpc"
    assert failed["reason"] == "invalid credentials"
    assert "paste-artifact" not in json.dumps(failed)


def test_unknown_state_is_failed(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["--json", "init"], env).returncode == 0

    def connect(token, address, answers):
        del address
        secret = (token or (answers or {}).get("credential") or "").strip()
        if not secret:
            return setup_needed(credential_option(), continuation={"phase": "wait"})
        return mailbox_view(ADDRESS, owned_address=True)

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    code, first = _connect(monkeypatch, capsys, env)
    assert code == 3
    assert _continuation_file(vault).is_file()
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
    assert _continuation_file(vault).is_file()
    code, done = _connect(
        monkeypatch,
        capsys,
        env,
        [
            "--continue",
            "--state",
            first["state"],
            "--result-file",
            cred,
        ],
    )
    assert code == 0, done
    assert done["status"] == "connected"


def test_env_connects_without_copying_into_identity_dir(
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
    missing = run_cli(["--json", "secret", "exists", "email.credential"], env)
    assert missing.returncode == 3
    assert json.loads(missing.stdout)["exists"] is False


@pytest.mark.parametrize(
    "credential_env",
    ["AGENTSELF_EMAIL_CREDENTIAL", "AGENTSELF_AGENTMAIL_API_KEY"],
)
def test_runtime_credential_env_sources_cover_all_email_operations(
    tmp_path: Path, monkeypatch, capsys, credential_env: str
) -> None:
    from agentself.internal.log import MemoryLog

    from tests.test_agentmail_mailbox import API, INBOXES, OURS, Http, _box

    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0
    http = Http()
    inbox_id = "inb_runtime_env"
    http.on_get(
        INBOXES,
        200,
        {"inboxes": [{"inbox_id": inbox_id, "email": OURS}]},
    )
    http.on_get(
        f"{API}/v0/inboxes/{inbox_id}/messages",
        200,
        {"messages": []},
    )

    def for_binding(self, binding: str):
        del binding
        return _box(Path(env["AGENTSELF_IDENTITY_DIR"]), MemoryLog(), http)

    monkeypatch.setattr(
        "agentself.compose.MailboxAccessFactory.for_binding",
        for_binding,
    )
    apply_cli_env(monkeypatch, env)
    monkeypatch.setenv("AGENTSELF_EMAIL_ADDRESS", OURS)
    monkeypatch.setenv(credential_env, CREDENTIAL)

    assert main(["email", "show"]) == 0
    assert main(["email", "send", "to@example.com", "subject", "body"]) == 0
    assert main(["email", "receive"]) == 0
    assert main(["email", "list"]) == 0
    captured = capsys.readouterr()
    assert CREDENTIAL not in captured.out + captured.err
    assert http.posts
    assert http.gets

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
    assert "email.credential" not in names


def test_imap_alias_env_covers_send_receive_list(tmp_path: Path, monkeypatch, capsys):
    from agentself.internal.log import MemoryLog

    from tests.test_imap_mailbox import ADDRESS, TO, FakeImap, FakeSmtp, _box

    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init", "--email", "imap"], env).returncode == 0
    imap = FakeImap()
    smtp = FakeSmtp()
    mailbox = _box(Path(env["AGENTSELF_IDENTITY_DIR"]), MemoryLog(), imap, smtp)

    def for_binding(self, binding: str):
        del binding
        return mailbox

    monkeypatch.setattr(
        "agentself.compose.MailboxAccessFactory.for_binding",
        for_binding,
    )
    apply_cli_env(monkeypatch, env)
    monkeypatch.setenv("AGENTSELF_EMAIL_ADDRESS", ADDRESS)
    monkeypatch.setenv("AGENTSELF_MAIL_PASSWORD", CREDENTIAL)
    monkeypatch.delenv("AGENTSELF_EMAIL_CREDENTIAL", raising=False)
    monkeypatch.setenv("AGENTSELF_EMAIL_BACKEND", "imap")

    assert main(["email", "show"]) == 0
    assert main(["email", "send", TO, "subject", "body"]) == 0
    assert main(["email", "receive"]) == 0
    assert main(["email", "list"]) == 0
    captured = capsys.readouterr()
    assert CREDENTIAL not in captured.out + captured.err
    assert smtp.logins
    assert imap.logins


@pytest.mark.parametrize(
    "persist_as",
    [WALLET_KEY_NAME, EMAIL_CONTINUATION_NAME, "not a token"],
)
def test_persist_as_refuses_reserved_protected_and_invalid(
    tmp_path: Path, monkeypatch, capsys, persist_as: str
) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["--json", "init"], env).returncode == 0
    got = run_cli(
        ["--json", "secret", "get", WALLET_KEY_NAME, "--unsafe", "--print"], env
    )
    assert got.returncode == 0, got.stdout + got.stderr
    wallet = json.loads(got.stdout)["value"]
    option = credential_option(persist=True, persist_as=persist_as)

    def connect(token, address, answers):
        del address
        secret = (token or (answers or {}).get("credential") or "").strip()
        if not secret:
            return setup_needed(option, continuation={"phase": "wait"})
        return mailbox_view(ADDRESS, owned_address=True)

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect, options=(option,)))
    code, first = _connect(monkeypatch, capsys, env)
    assert code == 3
    cred = value_file(tmp_path, CREDENTIAL, "credential.txt")
    code, refused = _connect(
        monkeypatch,
        capsys,
        env,
        [
            "--continue",
            "--state",
            first["state"],
            "--result-file",
            cred,
        ],
    )
    assert code == 2
    assert refused["ok"] is False
    assert refused["error"] == "refused"
    after_proc = run_cli(
        ["--json", "secret", "get", WALLET_KEY_NAME, "--unsafe", "--print"], env
    )
    assert after_proc.returncode == 0, after_proc.stdout + after_proc.stderr
    after = json.loads(after_proc.stdout)["value"]
    assert after == wallet
    assert CREDENTIAL not in after


def test_rpc_mailbox_error_keeps_continuation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["--json", "init"], env).returncode == 0
    n = {"i": 0}

    def connect(token, address, answers):
        del address
        n["i"] += 1
        secret = (token or (answers or {}).get("credential") or "").strip()
        if n["i"] == 1:
            return setup_needed(credential_option(), continuation={"phase": "wait"})
        if n["i"] == 2:
            raise MailboxError("rpc failed")
        if not secret:
            return setup_needed(credential_option(), continuation={"phase": "wait"})
        return mailbox_view(ADDRESS, owned_address=True)

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    code, first = _connect(monkeypatch, capsys, env)
    assert code == 3
    assert _continuation_file(vault).is_file()
    cred = value_file(tmp_path, CREDENTIAL, "credential.txt")
    extra = [
        "--continue",
        "--state",
        first["state"],
        "--result-file",
        cred,
    ]
    code, failed = _connect(monkeypatch, capsys, env, extra)
    assert code == 1
    assert failed["ok"] is False
    assert failed["reason"] == "rpc"
    assert _continuation_file(vault).is_file()
    code, done = _connect(monkeypatch, capsys, env, extra)
    assert code == 0, done
    assert done["status"] == "connected"
    assert not _continuation_file(vault).is_file()


def test_terminal_mailbox_error_deletes_continuation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["--json", "init"], env).returncode == 0
    n = {"i": 0}

    def connect(_token, _address, _answers):
        n["i"] += 1
        if n["i"] == 1:
            return setup_needed(credential_option(), continuation={"phase": "wait"})
        raise MailboxError("invalid credentials")

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    code, first = _connect(monkeypatch, capsys, env)
    assert code == 3
    assert _continuation_file(vault).is_file()
    cred = value_file(tmp_path, CREDENTIAL, "credential.txt")
    code, failed = _connect(
        monkeypatch,
        capsys,
        env,
        [
            "--continue",
            "--state",
            first["state"],
            "--result-file",
            cred,
        ],
    )
    assert code == 1
    assert failed["reason"] == "invalid credentials"
    assert not _continuation_file(vault).is_file()

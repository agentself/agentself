"""Opaque email continuation and declared persistence via a test-only mailbox."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentself.cli.app import main
from agentself.internal.files import secrets_home
from agentself.internal.names import EMAIL_CONTINUATION_NAME, EMAIL_CREDENTIAL_NAME
from agentself.internal.setup import encode_state

from tests.support import apply_cli_env, cli_env, run_cli, value_file
from tests.synthetic_email import (
    CONTINUATION_CANARY,
    CREDENTIAL_CANARY,
    SyntheticEmailAccess,
)

LABEL = "desk"
LABEL_NAME = "email.oauthish.label"


def _patch_mailbox(monkeypatch, mailbox: SyntheticEmailAccess | None = None):
    box = mailbox or SyntheticEmailAccess()
    monkeypatch.setattr(
        "agentself.compose.MailboxAccessFactory.for_binding",
        lambda self, binding: box,
    )
    return box


def _connect(
    monkeypatch, capsys, env: dict[str, str], extra: list[str] | None = None
) -> tuple[int, dict, str]:
    apply_cli_env(monkeypatch, env)
    cred_env = env.get("AGENTSELF_EMAIL_CREDENTIAL")
    if cred_env:
        monkeypatch.setenv("AGENTSELF_EMAIL_CREDENTIAL", cred_env)
    else:
        monkeypatch.delenv("AGENTSELF_EMAIL_CREDENTIAL", raising=False)
    # JSON continue without --result-file must not read pytest's captured stdin.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    code = main(["--json", "email", "connect", *(extra or [])])
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    data = json.loads(captured.out or captured.err or "{}")
    return code, data, blob


def _continuation_file(vault: Path, identity_id: str = "agent") -> Path:
    return secrets_home(vault, identity_id) / f"{EMAIL_CONTINUATION_NAME}.sops"


def test_oauthish_connect_round_trips_continuation_and_declared_persist(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["--json", "init"], env).returncode == 0
    SyntheticEmailAccess.reset()
    _patch_mailbox(monkeypatch)
    env["AGENTSELF_EMAIL_CREDENTIAL"] = CREDENTIAL_CANARY

    code, first, first_blob = _connect(monkeypatch, capsys, env)
    assert code == 3
    assert first["status"] == "action_required"
    assert first["human_action_required"] is True
    assert first["state"]
    assert first["continue"].startswith("agentself email connect --continue --state ")
    assert "option" not in first or first.get("option") is None
    assert "continuation" not in first
    assert "phase" not in first
    assert "refresh" not in first
    assert CONTINUATION_CANARY not in first_blob
    assert CREDENTIAL_CANARY not in first_blob
    assert _continuation_file(vault).is_file()
    issued = list(SyntheticEmailAccess.issued)
    assert issued
    assert issued[0]["phase"] == "action"
    assert issued[0]["refresh"] == CONTINUATION_CANARY

    code, pending, pending_blob = _connect(
        monkeypatch,
        capsys,
        env,
        ["--continue", "--state", first["state"]],
    )
    assert code == 3, pending
    assert pending["status"] == "pending"
    assert pending["state"] != first["state"]
    assert "continuation" not in pending
    assert CONTINUATION_CANARY not in pending_blob
    assert CREDENTIAL_CANARY not in pending_blob
    assert SyntheticEmailAccess.received_states[1] == issued[0]

    code, needed, needed_blob = _connect(
        monkeypatch,
        capsys,
        env,
        ["--continue", "--state", pending["state"]],
    )
    assert code == 3
    assert needed["status"] == "input_required"
    assert needed["option"]["name"] == "label"
    assert needed["option"].get("persist") is None
    assert CONTINUATION_CANARY not in needed_blob
    assert CREDENTIAL_CANARY not in needed_blob
    assert SyntheticEmailAccess.received_states[2] == SyntheticEmailAccess.issued[1]

    label = value_file(tmp_path, LABEL, "label.txt")
    code, done, done_blob = _connect(
        monkeypatch,
        capsys,
        env,
        [
            "--continue",
            "--state",
            needed["state"],
            "--result-file",
            label,
        ],
    )
    assert code == 0, done
    assert done["ok"] is True
    assert done["status"] == "connected"
    assert done["address"] == f"{LABEL}@example.com"
    assert "continuation" not in done
    assert CONTINUATION_CANARY not in done_blob
    assert CREDENTIAL_CANARY not in done_blob
    assert json.dumps(done).count("CANARY") == 0
    assert not _continuation_file(vault).is_file()

    names = json.loads(run_cli(["--json", "secret", "list"], env).stdout)["names"]
    assert LABEL_NAME in names
    assert EMAIL_CREDENTIAL_NAME not in names
    assert EMAIL_CONTINUATION_NAME not in names
    got = run_cli(["--json", "secret", "get", LABEL_NAME], env)
    assert json.loads(got.stdout)["value"] == LABEL
    missing = run_cli(["--json", "secret", "exists", EMAIL_CREDENTIAL_NAME], env)
    assert missing.returncode == 3
    assert json.loads(missing.stdout)["exists"] is False


def test_oauthish_result_file_credential_persists_and_canary_stays_off_cli(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["--json", "init"], env).returncode == 0
    SyntheticEmailAccess.reset()
    _patch_mailbox(monkeypatch)

    code, first, first_blob = _connect(monkeypatch, capsys, env)
    assert code == 3
    assert first["status"] == "action_required"
    _, pending, _ = _connect(
        monkeypatch, capsys, env, ["--continue", "--state", first["state"]]
    )
    _, needed, _ = _connect(
        monkeypatch, capsys, env, ["--continue", "--state", pending["state"]]
    )
    label = value_file(tmp_path, LABEL, "label.txt")
    code, cred_needed, cred_blob = _connect(
        monkeypatch,
        capsys,
        env,
        ["--continue", "--state", needed["state"], "--result-file", label],
    )
    assert code == 3
    assert cred_needed["status"] == "input_required"
    assert cred_needed["option"]["name"] == "credential"
    assert CONTINUATION_CANARY not in cred_blob
    assert CREDENTIAL_CANARY not in cred_blob + first_blob

    cred = value_file(tmp_path, CREDENTIAL_CANARY, "credential.txt")
    code, done, done_blob = _connect(
        monkeypatch,
        capsys,
        env,
        [
            "--continue",
            "--state",
            cred_needed["state"],
            "--result-file",
            cred,
        ],
    )
    assert code == 0, done
    assert done["status"] == "connected"
    assert CONTINUATION_CANARY not in done_blob
    assert CREDENTIAL_CANARY not in done_blob
    assert not _continuation_file(vault).is_file()
    names = json.loads(run_cli(["--json", "secret", "list"], env).stdout)["names"]
    assert LABEL_NAME in names
    assert EMAIL_CREDENTIAL_NAME in names
    listed = run_cli(["--json", "secret", "list"], env)
    assert CREDENTIAL_CANARY not in listed.stdout + listed.stderr
    got = run_cli(["--json", "secret", "get", EMAIL_CREDENTIAL_NAME], env)
    assert json.loads(got.stdout)["value"] == CREDENTIAL_CANARY
    assert CREDENTIAL_CANARY not in json.dumps(done)


def test_oauthish_backend_stays_put_without_continuation_blob() -> None:
    SyntheticEmailAccess.reset()
    mailbox = SyntheticEmailAccess()
    first = mailbox.connect("agent")
    assert first["status"] == "action_required"
    stuck = mailbox.connect("agent")
    assert stuck["status"] == "action_required"
    advanced = mailbox.connect("agent", state=first["continuation"])
    assert advanced["status"] == "pending"
    assert mailbox.received_states[2] == first["continuation"]


def test_forged_or_unknown_setup_state_fails(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["--json", "init"], env).returncode == 0
    SyntheticEmailAccess.reset()
    _patch_mailbox(monkeypatch)
    code, first, first_blob = _connect(monkeypatch, capsys, env)
    assert code == 3
    assert first["status"] == "action_required"
    assert _continuation_file(vault).is_file()
    forged = encode_state({"n": "not-a-stored-nonce"})
    code, unknown, blob = _connect(
        monkeypatch,
        capsys,
        env,
        ["--continue", "--state", forged],
    )
    assert code == 1
    assert unknown["ok"] is False
    assert unknown["status"] == "failed"
    assert unknown["reason"] == "unknown setup"
    assert CONTINUATION_CANARY not in blob + first_blob
    assert CREDENTIAL_CANARY not in blob + first_blob
    assert _continuation_file(vault).is_file()
    code, pending, pending_blob = _connect(
        monkeypatch,
        capsys,
        env,
        ["--continue", "--state", first["state"]],
    )
    assert code == 3, pending
    assert pending["status"] == "pending"
    assert CONTINUATION_CANARY not in pending_blob
    assert _continuation_file(vault).is_file()

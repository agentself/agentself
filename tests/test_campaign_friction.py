"""Issue #60 campaign follow-up: identity next, mail files, diagnose, lock."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from agentself.cli.app import main
from agentself.cli.commands.email import list_email, send_email
from agentself.cli.outcomes import CliSuccess
from agentself.internal.files import IdentityBusy, identity_home
from agentself.internal.mail_state import MAIL_LIST_CAP, MailRefState

from tests.support import PROJECT_ROOT, apply_cli_env, cli_env, run_cli, value_file


def _isolated_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        dict.fromkeys([str(PROJECT_ROOT), *(path for path in sys.path if path)])
    )
    env["AGENTSELF_FETCH_TOOLS"] = "0"
    env["AGENTSELF_FORBID_LIVE_AGENTMAIL"] = "1"
    for key in (
        "AGENTSELF_IDENTITY_DIR",
        "AGENTSELF_IDENTITY_ID",
        "AGE_KEY_FILE",
        "AGENTSELF_MAIL_DOMAIN",
        "AGENTSELF_EMAIL_BACKEND",
        "AGENTSELF_WALLET_BACKEND",
        "AGENTSELF_EMAIL_ADDRESS",
        "AGENTSELF_EMAIL_CREDENTIAL",
        "AGENTSELF_AGENTMAIL_API_KEY",
        "AGENTSELF_MAIL_PASSWORD",
        "AGENTSELF_ETH_RPC_URL",
    ):
        env.pop(key, None)
    return env


def test_flagged_missing_identity_names_identity_dir(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = _isolated_env()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    missing = tmp_path / "flagged"
    proc = run_cli(["--identity-dir", str(missing), "show"], env)
    assert proc.returncode == 2
    data = json.loads(proc.stdout)
    assert data["reason"] == "not initialized"
    assert data["next"] == f"agentself --identity-dir {missing} init"


def test_bare_restore_refuses_when_default_identity_is_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = _isolated_env()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    isolated = tmp_path / "isolated"
    assert run_cli(["--identity-dir", str(isolated), "init"], env).returncode == 0
    backup = tmp_path / "backup"
    copied = run_cli(["--identity-dir", str(isolated), "backup", str(backup)], env)
    assert copied.returncode == 0, copied.stderr
    proc = run_cli(["restore", str(backup)], env)
    assert proc.returncode == 2
    data = json.loads(proc.stdout)
    assert data["reason"] == "identity directory is missing"
    assert data["next"] == "agentself --identity-dir PATH restore PATH"
    assert not (home / ".agentself" / "config.json").exists()


def test_note_set_lock_timeout_is_identity_directory_busy(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["init"], env).returncode == 0
    apply_cli_env(monkeypatch, env)

    def boom(*_args, **_kwargs):
        raise IdentityBusy()

    monkeypatch.setattr("agentself.internal.notes.exclusive", boom)
    code = main(["note", "set", "handoff", "next"])
    captured = capsys.readouterr()
    assert code == 1
    data = json.loads(captured.out)
    assert captured.err == ""
    assert data["error"] == "error"
    assert data["reason"] == "identity directory busy"
    assert data["next"] == "agentself diagnose"


def test_email_send_file_is_accepted_and_stdin_is_not_implicit(
    tmp_path: Path,
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["init"], env).returncode == 0
    body = value_file(tmp_path, "secret-body\n", "body.txt")
    sent = run_cli(
        ["email", "send", "--file", body, "someone@example.com", "hello"],
        env,
    )
    assert sent.returncode == 1
    data = json.loads(sent.stdout)
    assert "unrecognized arguments" not in data["reason"]
    assert data["reason"] == "not_ready"
    missing = run_cli(["email", "send", "someone@example.com", "hello"], env)
    assert missing.returncode == 3
    assert json.loads(missing.stdout)["reason"] == "need a value"
    both = run_cli(
        [
            "email",
            "send",
            "someone@example.com",
            "hello",
            "argv-body",
            "--file",
            body,
        ],
        env,
    )
    assert both.returncode == 2
    assert "body and --file" in json.loads(both.stdout)["reason"]


def test_email_mark_rejected_is_not_acted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr
    MailRefState(vault).remember("agent", "provider/lure")
    marked = run_cli(["email", "mark", "provider/lure", "rejected"], env)
    assert marked.returncode == 0, marked.stdout
    data = json.loads(marked.stdout)
    assert data["acted"] is False
    assert data["rejected"] is True
    digest_dir = identity_home(vault, "agent") / "email" / "rejected"
    assert any(digest_dir.iterdir())
    acted = identity_home(vault, "agent") / "email" / "acted"
    assert not acted.exists() or not any(acted.iterdir())


def test_email_list_limit_is_refused_out_of_range(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["init"], env).returncode == 0
    proc = run_cli(["email", "list", "--limit", "0"], env)
    assert proc.returncode == 2
    data = json.loads(proc.stdout)
    assert data["reason"] == "limit must be 1..100"
    assert data["next"] == "agentself email list --help"
    receive = run_cli(["email", "receive", "--limit", "0"], env)
    assert receive.returncode == 2
    assert json.loads(receive.stdout)["next"] == "agentself email receive --help"


def test_diagnose_uninitialized_and_mid_connect_name_next(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    fresh = json.loads(run_cli(["diagnose"], env).stdout)
    assert fresh["initialized"] is False
    assert fresh["next"] == "agentself init"
    assert run_cli(["init"], env).returncode == 0
    ready = json.loads(run_cli(["diagnose"], env).stdout)
    assert ready["ok"] is True
    assert ready["ready"]["email"] is False
    assert ready["ready"]["wallet"] is True
    assert ready["next"] == "agentself email connect"


def test_diagnose_ethereum_without_rpc_is_not_wallet_ready(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    env.pop("AGENTSELF_ETH_RPC_URL", None)
    started = run_cli(["init", "--wallet", "ethereum"], env)
    assert started.returncode == 0, started.stderr
    proc = run_cli(["diagnose"], env)
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["ready"]["wallet"] is False
    assert "AGENTSELF_ETH_RPC_URL" in data["reason"]
    assert data["next"] == "set AGENTSELF_ETH_RPC_URL"


def test_backends_ethereum_marks_rpc_url_required(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    data = json.loads(run_cli(["backends", "wallet", "ethereum"], env).stdout)
    options = data["channel"]["backends"][0]["options"]
    rpc = next(item for item in options if item["name"] == "rpc_url")
    assert rpc["required"] is True


def test_commands_next_points_at_receive_when_email_is_ready(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["init"], env).returncode == 0
    catalog = json.loads(run_cli(["commands"], env).stdout)
    email = next(item for item in catalog["commands"] if item["name"] == "email")
    assert email["next"] == "agentself email connect"
    apply_cli_env(monkeypatch, env)
    monkeypatch.setattr(
        "agentself.cli.commands.catalog._email_catalog_next",
        lambda _vault: "agentself email receive",
    )
    assert main(["commands"]) == 0
    ready = json.loads(capsys.readouterr().out)
    email = next(item for item in ready["commands"] if item["name"] == "email")
    assert email["next"] == "agentself email receive"
    assert main(["backends", "email"]) == 0
    backends = json.loads(capsys.readouterr().out)
    assert backends["channel"]["next"] == "agentself email receive"


def test_email_send_file_returns_id_and_ref(tmp_path: Path, monkeypatch) -> None:
    body = value_file(tmp_path, "hello\n", "send-body.txt")

    class Client:
        def email_send(self, to, subject, text):
            assert (to, subject) == ("a@example.com", "hello")
            assert text.startswith("hello")
            return {"id": "provider/out", "ref": "m1"}

    monkeypatch.setattr("agentself.cli.commands.email.client", lambda _vault: Client())
    args = SimpleNamespace(
        to="a@example.com",
        subject="hello",
        body=None,
        from_file=body,
        email_command="send",
    )
    outcome = send_email(args, tmp_path)
    assert isinstance(outcome, CliSuccess)
    assert outcome.payload["id"] == "provider/out"
    assert outcome.payload["ref"] == "m1"
    assert outcome.payload["to"] == "a@example.com"


def test_email_list_marks_truncated_at_cap(tmp_path: Path, monkeypatch) -> None:
    class Client:
        def email_list(self, *, status=None, acted=None, rejected=None, limit=None):
            count = limit if limit is not None else MAIL_LIST_CAP
            return [
                {"id": f"id-{i}", "acted": False, "rejected": False}
                for i in range(count)
            ]

    monkeypatch.setattr("agentself.cli.commands.email.client", lambda _vault: Client())
    args = SimpleNamespace(
        status=None,
        acted_filter=None,
        limit=None,
        email_command="list",
    )
    outcome = list_email(args, tmp_path)
    assert isinstance(outcome, CliSuccess)
    assert len(outcome.payload["messages"]) == MAIL_LIST_CAP
    assert outcome.payload["truncated"] is True
    args.limit = 10
    limited = list_email(args, tmp_path)
    assert isinstance(limited, CliSuccess)
    assert len(limited.payload["messages"]) == 10
    assert limited.payload["truncated"] is True

"""Agent-CLI additions: _next, command schemas, poll, --test, --force, sanitize."""

from __future__ import annotations

import json
from pathlib import Path

from agentself.backends.email.contract import mailbox_view, setup_needed
from agentself.cli.app import main
from agentself.internal.next import next_object
from agentself.internal.sanitize import sanitize_text
from agentself.internal.setup import SETUP_ACTION_REQUIRED

from tests.support import (
    MockRpc,
    apply_cli_env,
    cli_env,
    compose_with_rpc,
    run_cli,
    symlink_or_skip,
    value_file,
)
from tests.test_email_setup_protocol import ScriptedMailbox, _connect, _patch_mailbox

ADDRESS = "agent@example.com"
_TO = "0x" + "11" * 20


def test_next_object_only_for_agentself_commands() -> None:
    assert next_object("fund ETH") is None
    assert next_object("set AGENTSELF_ETH_RPC_URL") is None
    assert next_object("agentself diagnose") == {"command": "agentself diagnose"}
    assert next_object(
        "agentself email connect --continue --state S --interval 5",
        until="status is connected",
        interval=5,
    ) == {
        "command": "agentself email connect --continue --state S --interval 5",
        "until": "status is connected",
        "poll_interval_seconds": 5.0,
    }


def test_sanitize_text_strips_ansi_and_keeps_newlines() -> None:
    raw = "Hello\x1b[31mRED\x1b[0m\nworld\x07"
    assert sanitize_text(raw) == "HelloRED\nworld"


def test_commands_default_schema_includes_params(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    data = json.loads(run_cli(["commands"], env).stdout)
    email = next(item for item in data["commands"] if item["name"] == "email")
    connect = next(verb for verb in email["verbs"] if verb["name"] == "connect")
    names = {item["name"] for item in connect["params"]}
    assert "--interval" in names
    assert "--timeout" in names
    assert "--result-file" in names
    result_file = next(
        item for item in connect["params"] if item["name"] == "--result-file"
    )
    assert result_file["sensitive"] is True
    wallet = next(item for item in data["commands"] if item["name"] == "wallet")
    send = next(verb for verb in wallet["verbs"] if verb["name"] == "send")
    send_names = {item["name"] for item in send["params"]}
    assert "--test" in send_names
    assert "--file" in send_names
    assert "TO" in send_names
    balance = next(verb for verb in wallet["verbs"] if verb["name"] == "balance")
    assert {item["name"] for item in balance["params"]} == {"ASSET"}
    blob = json.dumps(data)
    assert "value" not in blob
    assert "0x" not in blob


def test_failure_next_object_and_host_action_stays_string(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    missing = json.loads(run_cli(["show"], env).stdout)
    assert missing["ok"] is False
    assert missing["next"].startswith("agentself ")
    assert missing["_next"]["command"] == missing["next"]
    assert run_cli(["init"], env).returncode == 0
    apply_cli_env(monkeypatch, env)
    compose_with_rpc(monkeypatch, MockRpc(eth_wei=0))
    code = main(["wallet", "send", _TO, "1"])
    captured = capsys.readouterr()
    assert code == 2, captured.out + captured.err
    data = json.loads(captured.out)
    assert data["next"] == "fund ETH"
    assert "_next" not in data


def test_wallet_send_test_does_not_broadcast(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    started = json.loads(run_cli(["init"], env).stdout)
    rpc = MockRpc(eth_wei=10**18, usdc_raw=1_500_000)
    apply_cli_env(monkeypatch, env)
    compose_with_rpc(monkeypatch, rpc)
    code = main(["wallet", "send", started["address"], "1", "--test"])
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    data = json.loads(captured.out)
    assert data["ok"] is True
    assert data["test"] is True
    assert data["to"] == started["address"]
    assert data["amount"] == "1"
    assert data["asset"]
    assert "hash" not in data
    assert rpc.broadcast is False
    assert not any(method == "eth_sendRawTransaction" for method, _ in rpc.calls)
    assert not any(method == "eth_getTransactionCount" for method, _ in rpc.calls)


def test_secret_file_refuses_existing_without_force(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["init"], env).returncode == 0
    src = value_file(tmp_path, "secret-one", "one.txt")
    assert run_cli(["secret", "create", "notes", "--file", src], env).returncode == 0
    dest = tmp_path / "out.txt"
    dest.write_text("old\n", encoding="utf-8")
    refused = run_cli(["secret", "get", "notes", "--file", str(dest)], env)
    assert refused.returncode == 2
    data = json.loads(refused.stdout)
    assert data["reason"] == "file exists"
    assert dest.read_text(encoding="utf-8") == "old\n"
    forced = run_cli(["secret", "get", "notes", "--file", str(dest), "--force"], env)
    assert forced.returncode == 0
    assert dest.read_text(encoding="utf-8") == "secret-one"


def test_secret_file_refuses_symlink_even_with_force(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["init"], env).returncode == 0
    src = value_file(tmp_path, "secret-one", "one.txt")
    assert run_cli(["secret", "create", "notes", "--file", src], env).returncode == 0
    target = tmp_path / "target.txt"
    target.write_text("other\n", encoding="utf-8")
    dest = tmp_path / "out-link.txt"
    symlink_or_skip(dest, target)
    refused = run_cli(["secret", "get", "notes", "--file", str(dest), "--force"], env)
    assert refused.returncode == 2
    data = json.loads(refused.stdout)
    assert data["reason"] == "file is a symlink"
    assert "--force" not in data["next"]
    assert dest.is_symlink()
    assert target.read_text(encoding="utf-8") == "other\n"


def test_email_headers_drop_ansi(tmp_path: Path, monkeypatch, capsys) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["init"], env).returncode == 0
    apply_cli_env(monkeypatch, env)

    class _Access:
        def email_list(self, **_kwargs):
            return [
                {
                    "id": "message-1",
                    "subject": "Pay\x1b[31m now",
                    "from": "evil\x07@example.com",
                }
            ]

    monkeypatch.setattr("agentself.cli.commands.email.client", lambda _vault: _Access())
    assert main(["email", "list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    subject = listed["messages"][0]["subject"]
    assert "\x1b" not in subject
    assert subject == "Pay now"
    assert listed["messages"][0]["from"] == "evil@example.com"


def test_connect_interval_rejects_nonfinite(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["init"], env).returncode == 0
    for value in ("nan", "inf", "-inf"):
        proc = run_cli(["email", "connect", f"--interval={value}"], env)
        assert proc.returncode == 2, proc.stdout
        data = json.loads(proc.stdout)
        assert data["reason"] == "interval must be >= 0"


def test_connect_interval_polls_only_on_continue(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["init"], env).returncode == 0
    hits = {"n": 0}

    def connect(_token, _address, _answers):
        hits["n"] += 1
        if hits["n"] < 3:
            return setup_needed(
                status=SETUP_ACTION_REQUIRED,
                human_action_required=True,
                message="Open \x1b[31mapp",
            )
        return mailbox_view(ADDRESS, owned_address=True)

    _patch_mailbox(monkeypatch, ScriptedMailbox(connect))
    sleeps: list[float] = []
    monkeypatch.setattr("agentself.cli.commands.email.time.sleep", sleeps.append)
    code, first = _connect(monkeypatch, capsys, env, ["--interval", "5"])
    assert code == 3
    assert first["status"] == "action_required"
    assert first["message"] == "Open app"
    assert sleeps == []
    assert first["_next"]["command"].endswith("--interval 5")
    assert first["_next"]["poll_interval_seconds"] == 5
    assert first["_next"]["until"] == "status is connected"
    code, done = _connect(
        monkeypatch,
        capsys,
        env,
        [
            "--continue",
            "--state",
            first["state"],
            "--interval",
            "0.01",
            "--timeout",
            "2",
        ],
    )
    assert code == 0, done
    assert done["status"] == "connected"
    assert done["address"] == ADDRESS
    assert sleeps


def test_authorize_out_refuses_existing_file(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["init"], env).returncode == 0
    dest = tmp_path / "auth.txt"
    dest.write_text("old\n", encoding="utf-8")
    statement = tmp_path / "msg.txt"
    statement.write_text("hello\n", encoding="utf-8")
    refused = run_cli(
        ["wallet", "authorize", "--file", str(statement), "--out", str(dest)],
        env,
    )
    assert refused.returncode == 2
    assert json.loads(refused.stdout)["reason"] == "file exists"
    assert dest.read_text(encoding="utf-8") == "old\n"

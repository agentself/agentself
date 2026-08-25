"""Wallet verification against valid, modified, mismatched, and restored identities."""

from __future__ import annotations

import json
from pathlib import Path

from tests.support import cli_env, run_cli, value_file

MESSAGE = "prove custody"


def test_wallet_message_ingest_taxonomy(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0
    source = tmp_path / "message.txt"
    source.write_text(MESSAGE, encoding="utf-8")
    missing_path = str(tmp_path / "missing.txt")

    missing = run_cli(["--json", "wallet", "authorize"], env)
    assert missing.returncode == 3
    assert json.loads(missing.stdout)["error"] == "missing"
    assert json.loads(missing.stdout)["reason"] == "need a value"

    clash = run_cli(
        ["--json", "wallet", "authorize", MESSAGE, "--file", str(source)], env
    )
    assert clash.returncode == 2
    assert json.loads(clash.stdout)["error"] == "refused"
    assert json.loads(clash.stdout)["reason"] == "message and --file"

    missing_file = run_cli(
        ["--json", "wallet", "authorize", "--file", missing_path], env
    )
    assert missing_file.returncode == 1
    assert json.loads(missing_file.stdout)["error"] == "error"
    assert json.loads(missing_file.stdout)["reason"] == "file"

    missing_verify = run_cli(["--json", "wallet", "verify"], env)
    assert missing_verify.returncode == 3
    assert json.loads(missing_verify.stdout)["error"] == "missing"
    assert json.loads(missing_verify.stdout)["reason"] == "need a value"

    clash_verify = run_cli(
        [
            "--json",
            "wallet",
            "verify",
            MESSAGE,
            "0x" + "00" * 65,
            "--file",
            str(source),
        ],
        env,
    )
    assert clash_verify.returncode == 2
    assert json.loads(clash_verify.stdout)["error"] == "refused"
    assert json.loads(clash_verify.stdout)["reason"] == "message and --file"

    missing_file_verify = run_cli(
        ["--json", "wallet", "verify", "--file", missing_path, "0x" + "00" * 65],
        env,
    )
    assert missing_file_verify.returncode == 1
    assert json.loads(missing_file_verify.stdout)["error"] == "error"
    assert json.loads(missing_file_verify.stdout)["reason"] == "file"


def test_wallet_verify_valid_modified_mismatched_restored(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0
    msg = tmp_path / "message.txt"
    msg.write_text(MESSAGE, encoding="utf-8")
    auth = json.loads(
        run_cli(["--json", "wallet", "authorize", "--file", str(msg)], env).stdout
    )
    for key in ("address", "scheme", "network", "message_sha256", "authorization"):
        assert key in auth
    assert auth["scheme"] == "eip191"
    ok = json.loads(
        run_cli(
            ["--json", "wallet", "verify", "--file", str(msg), auth["authorization"]],
            env,
        ).stdout
    )
    assert ok["ok"] is True
    assert ok["valid"] is True
    assert ok["address"] == auth["address"]

    failed = run_cli(
        ["--json", "wallet", "verify", "--file", str(msg), "0x" + "00" * 65],
        env,
    )
    assert failed.returncode == 2
    assert json.loads(failed.stdout)["error"] == "refused"

    other = tmp_path / "other"
    other.mkdir()
    other_env = cli_env(other)
    assert run_cli(["--json", "init"], other_env).returncode == 0
    mismatched = run_cli(
        ["--json", "wallet", "verify", "--file", str(msg), auth["authorization"]],
        other_env,
    )
    assert mismatched.returncode == 2
    assert json.loads(mismatched.stdout)["error"] == "refused"

    backup = json.loads(
        run_cli(
            ["--json", "secret", "get", "wallet.key", "--unsafe"],
            env,
        ).stdout
    )
    key = backup["value"]
    restored = tmp_path / "restored"
    restored.mkdir()
    restored_env = cli_env(restored)
    assert run_cli(["--json", "init"], restored_env).returncode == 0
    updated = run_cli(
        [
            "--json",
            "secret",
            "update",
            "wallet.key",
            "--unsafe",
            "--file",
            value_file(tmp_path, key, "wallet.key.txt"),
        ],
        restored_env,
    )
    assert updated.returncode == 0, updated.stderr
    recovered = json.loads(
        run_cli(
            [
                "--json",
                "wallet",
                "verify",
                "--file",
                str(msg),
                auth["authorization"],
            ],
            restored_env,
        ).stdout
    )
    assert recovered["ok"] is True
    assert recovered["valid"] is True
    assert recovered["address"] == auth["address"]

"""Human CLI: init, show, aliases, missing tools."""

from __future__ import annotations

import json

from tests.support import cli_env, run_cli


def test_init_show_secrets_and_silent_aliases(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    first = run_cli(["init"], env)
    second = run_cli(["init"], env)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    for blob in (first.stdout, second.stdout):
        assert "age1" in blob
        assert "0x" in blob
        assert '"operation"' not in blob
        assert "email required" not in blob.lower()
    assert "AGE-SECRET-KEY" not in first.stdout + first.stderr + second.stdout

    shown = run_cli(["show"], env)
    bare = run_cli([], env)
    assert shown.returncode == 0, shown.stderr
    assert bare.returncode == 0, bare.stderr
    assert shown.stdout == bare.stdout
    assert "email: not configured" in shown.stdout
    assert "agentself email connect" in shown.stdout
    assert "agentself email set" not in shown.stdout
    assert f"vault: {vault}" in shown.stdout
    assert "AGE-SECRET-KEY" not in shown.stdout + shown.stderr

    created = run_cli(["secret", "create", "notes", "only I can open this"], env)
    assert created.returncode == 0, created.stderr
    got = run_cli(["secret", "get", "notes"], env)
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "only I can open this"
    listed = run_cli(["secret", "list"], env)
    assert listed.returncode == 0, listed.stderr
    assert "notes" in listed.stdout.splitlines()
    assert "only I can open this" not in listed.stdout
    clash = run_cli(["secret", "create", "notes", "nope"], env)
    assert clash.returncode == 2, clash.stdout + clash.stderr
    updated = run_cli(["secret", "update", "notes", "rotated"], env)
    assert updated.returncode == 0, updated.stderr
    assert run_cli(["secret", "get", "notes"], env).stdout.strip() == "rotated"

    wallet = run_cli(["wallet", "show"], env)
    assert wallet.returncode == 0, wallet.stderr
    assert wallet.stdout.strip().startswith("0x")
    assert len(wallet.stdout.strip()) == 42

    assert run_cli(["start"], env).returncode == 2
    assert run_cli(["set", "alias-notes", "via-set"], env).returncode == 2
    assert run_cli(["sms", "show"], env).returncode == 2
    assert run_cli(["recipient", "show"], env).returncode == 2
    assert run_cli(["doctor"], env).returncode == 2

    email_show = run_cli(["email", "show"], env)
    assert email_show.returncode == 0, email_show.stderr
    assert "not configured" in email_show.stdout
    email_json = run_cli(["--json", "email", "show"], env)
    assert email_json.returncode == 0, email_json.stderr
    email_data = json.loads(email_json.stdout)
    assert email_data.get("owned_address") is False
    assert email_data.get("address") in (None, "")


def test_uninitialized_status_points_to_init(tmp_path):
    env = cli_env(tmp_path / "vault")
    status = run_cli([], env)
    assert status.returncode == 2, status.stdout + status.stderr
    assert "not initialized" in status.stderr
    assert "agentself init" in status.stderr
    assert "agentself start" not in status.stderr
    shown = run_cli(["show"], env)
    assert shown.returncode == 2, shown.stdout + shown.stderr
    assert "not initialized" in shown.stderr
    assert "agentself init" in shown.stderr
    assert "agentself start" not in shown.stderr


def test_init_missing_age_keygen_one_error_line(tmp_path):
    env = cli_env(tmp_path / "vault")
    env["PATH"] = "/var/empty"
    proc = run_cli(["init"], env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr
    assert "Traceback" not in proc.stdout
    lines = [line for line in proc.stderr.splitlines() if line.strip()]
    assert "error" in proc.stderr.lower()
    assert "age" in proc.stderr.lower()
    assert "install --tools" in proc.stderr
    assert len(lines) == 2, proc.stderr

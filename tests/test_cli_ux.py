"""Human CLI: init, show, and the uninitialized next step."""

from __future__ import annotations

from tests.support import cli_env, run_cli


def test_init_show_and_bare_status(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    first = run_cli(["init"], env)
    second = run_cli(["init"], env)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    blob = first.stdout + first.stderr + second.stdout + second.stderr
    assert "age1" in first.stdout
    assert "0x" in first.stdout
    assert "AGE-SECRET-KEY" not in blob

    shown = run_cli(["show"], env)
    bare = run_cli([], env)
    assert shown.returncode == 0, shown.stderr
    assert bare.returncode == 0, bare.stderr
    assert shown.stdout == bare.stdout
    assert "email: not configured" in shown.stdout
    assert "agentself email connect" in shown.stdout
    assert f"identity_dir: {vault}" in shown.stdout
    assert "AGE-SECRET-KEY" not in shown.stdout + shown.stderr

    wallet = run_cli(["wallet", "show"], env)
    assert wallet.returncode == 0, wallet.stderr
    assert wallet.stdout.strip().startswith("0x")

    email_show = run_cli(["email", "show"], env)
    assert email_show.returncode == 0, email_show.stdout + email_show.stderr
    assert email_show.stdout.strip() == "not configured"


def test_uninitialized_status_points_to_init(tmp_path):
    env = cli_env(tmp_path / "vault")
    status = run_cli([], env)
    assert status.returncode == 2, status.stdout + status.stderr
    assert "not initialized" in status.stderr
    assert "agentself init" in status.stderr
    shown = run_cli(["show"], env)
    assert shown.returncode == 2, shown.stdout + shown.stderr
    assert "not initialized" in shown.stderr
    assert "agentself init" in shown.stderr

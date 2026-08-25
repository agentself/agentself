"""JSON CLI: init, show, and the uninitialized next step."""

from __future__ import annotations

import json

from tests.support import cli_env, run_cli


def test_init_show_and_bare_status(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    first = run_cli(["init"], env)
    second = run_cli(["init"], env)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stderr == ""
    started = json.loads(first.stdout)
    assert started["ok"] is True
    assert str(started["recipient"]).startswith("age1")
    assert str(started["address"]).startswith("0x")
    blob = first.stdout + first.stderr + second.stdout + second.stderr
    assert "AGE-SECRET-KEY" not in blob

    shown = run_cli(["show"], env)
    bare = run_cli([], env)
    assert shown.returncode == 0, shown.stderr
    assert bare.returncode == 0, bare.stderr
    assert shown.stdout == bare.stdout
    assert shown.stderr == ""
    data = json.loads(shown.stdout)
    assert data["ok"] is True
    assert data["identity_dir"] == str(vault)
    assert data["email"]["ready"] is False
    assert "AGE-SECRET-KEY" not in shown.stdout + shown.stderr

    hidden = run_cli(["--json", "show"], env)
    assert hidden.stdout == shown.stdout
    after = run_cli(["show", "--json"], env)
    assert after.stdout == shown.stdout

    wallet = run_cli(["wallet", "show"], env)
    assert wallet.returncode == 0, wallet.stderr
    assert json.loads(wallet.stdout)["address"].startswith("0x")
    raw_wallet = run_cli(["wallet", "show", "--raw"], env)
    assert raw_wallet.returncode == 0, raw_wallet.stderr
    assert raw_wallet.stdout == json.loads(wallet.stdout)["address"]
    assert raw_wallet.stderr == ""

    email_show = run_cli(["email", "show"], env)
    assert email_show.returncode == 0, email_show.stdout + email_show.stderr
    shown_email = json.loads(email_show.stdout)
    assert shown_email["ok"] is True
    assert shown_email["ready"] is False


def test_uninitialized_status_points_to_init(tmp_path):
    env = cli_env(tmp_path / "vault")
    status = run_cli([], env)
    assert status.returncode == 2, status.stdout + status.stderr
    assert status.stderr == ""
    data = json.loads(status.stdout)
    assert data["reason"] == "not initialized"
    assert data["next"] == "agentself init"
    shown = run_cli(["show"], env)
    assert shown.returncode == 2, shown.stdout + shown.stderr
    assert shown.stderr == ""
    assert json.loads(shown.stdout)["next"] == "agentself init"

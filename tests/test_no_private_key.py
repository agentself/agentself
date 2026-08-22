"""Gateway / Manager never return a private key."""

from __future__ import annotations

import json

from tests.support import cli_env, run_cli, setup_principal


def test_enroll_view_has_recipient_only(app, monkeypatch):
    key = setup_principal(app.vault, "P", store="sops")
    app.keys["P"] = key
    app.bind(monkeypatch, "P")
    view = app.gateway.enroll("sops")
    dumped = json.dumps(view)
    assert view["id"] == "P"
    assert view["recipient"].startswith("age1")
    assert "AGE-SECRET-KEY" not in dumped
    assert set(view.keys()) == {"id", "recipient"}
    private = key.read_text(encoding="utf-8")
    assert "AGE-SECRET-KEY" in private
    assert "AGE-SECRET-KEY" not in view["recipient"]
    for rec in app.log.records:
        assert "AGE-SECRET-KEY" not in json.dumps(rec)


def test_cli_start_stdout_has_recipient_not_private_key(tmp_path):
    env = cli_env(tmp_path / "vault")
    env["AGENTSELF_IDENTITY_ID"] = "P"
    proc = run_cli(["init", "--json"], env)
    assert proc.returncode == 0, proc.stderr
    assert "AGE-SECRET-KEY" not in proc.stdout
    assert "AGE-SECRET-KEY" not in proc.stderr
    view = json.loads(proc.stdout)
    assert view["ok"] is True
    assert view["id"] == "P"
    assert view["recipient"].startswith("age1")
    assert str(view["address"]).startswith("0x")
    assert "AGE-SECRET-KEY" not in view["recipient"]


def test_manager_enroll_principal_has_no_private_key(app, monkeypatch):
    key = setup_principal(app.vault, "P", store="sops")
    app.keys["P"] = key
    app.bind(monkeypatch, "P")
    from agentself.bind import bind_from_env

    principal = app.manager.enroll(bind_from_env(), "sops")
    assert not hasattr(principal, "private_key")
    assert "AGE-SECRET-KEY" not in principal.recipient
    assert "AGE-SECRET-KEY" not in json.dumps(principal.public_view())

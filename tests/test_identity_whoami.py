"""whoami / identity: domain requirement, no invented phone number, USDC address."""

from __future__ import annotations

from tests.support import build_app, cli_env, run_cli, setup_identity


def test_identity_without_domain_and_no_phone_number(app, monkeypatch):
    app.keys["P"] = setup_identity(app.vault, "P", store="sops")
    app.bind(monkeypatch, "P")
    app.client.init("sops")
    view = app.client.identity()
    assert view["id"] == "P"
    assert str(view["recipient"]).startswith("age1")
    email = view["email"]
    assert email["needs_domain"] is True
    assert email["owned_address"] is False
    assert email["address"] is None
    assert "sms" not in view
    wallet = view["wallet"]
    assert str(wallet["address"]).startswith("0x")
    assert wallet["asset"] == "USDC"
    assert wallet["chain"] == "base"


def test_identity_maildir_with_domain_does_not_invent_address(vault, monkeypatch):
    app = build_app(vault, mail_domain="example.com")
    app.keys["P"] = setup_identity(app.vault, "P", store="sops")
    app.bind(monkeypatch, "P")
    app.client.init("sops")
    view = app.client.identity()
    email = view["email"]
    assert email["owned_address"] is False
    assert email["address"] is None
    assert email["address"] != "P@example.com"
    assert "P@example.com" not in str(view)


def test_cli_identity_does_not_claim_a_phone_number(tmp_path):
    env = cli_env(tmp_path / "vault")
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    proc = run_cli(["show"], env)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    low = out.lower()
    assert "not configured" in low
    assert "0x" in out
    for banned in (
        "your phone number",
        "phone number:",
        "gives you a phone",
        "did:",
        "+1",
    ):
        assert banned not in low

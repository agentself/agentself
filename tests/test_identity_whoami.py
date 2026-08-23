"""Identity view does not invent an email address."""

from __future__ import annotations

from tests.support import build_app, init_identity


def test_identity_without_domain_has_no_email_address(app, monkeypatch):
    init_identity(app, monkeypatch)
    view = app.client.identity()
    assert view["id"] == "P"
    assert str(view["recipient"]).startswith("age1")
    email = view["email"]
    assert email["needs_domain"] is True
    assert email["owned_address"] is False
    assert email["address"] is None
    wallet = view["wallet"]
    assert str(wallet["address"]).startswith("0x")
    assert wallet["asset"] == "USDC"
    assert wallet["chain"] == "base"


def test_identity_with_mail_domain_does_not_invent_address(vault, monkeypatch):
    app = build_app(vault, mail_domain="example.com")
    init_identity(app, monkeypatch)
    view = app.client.identity()
    email = view["email"]
    assert email["owned_address"] is False
    assert email["address"] is None
    assert "P@example.com" not in str(view)

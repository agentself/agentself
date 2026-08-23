"""Client init never returns a private key."""

from __future__ import annotations

import json

from tests.support import setup_identity


def test_init_view_has_recipient_only(app, monkeypatch):
    key = setup_identity(app.vault, "P", store="sops")
    app.keys["P"] = key
    app.bind(monkeypatch, "P")
    view = app.client.init("sops")
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

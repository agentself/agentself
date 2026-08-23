"""Logs never contain secret values."""

from __future__ import annotations

import json
import uuid

from tests.support import setup_identity


def test_logs_never_contain_canary_value(app, monkeypatch):
    key = setup_identity(app.vault, "P", store="sops")
    app.keys["P"] = key
    app.bind(monkeypatch, "P")
    app.client.init("sops")
    canary = f"CANARY-{uuid.uuid4()}-SECRET"
    app.client.create("token", canary)
    app.client.get("token")
    app.client.update("token", canary + "-rotated")
    app.client.list()

    sink = app.log.rendered()
    assert canary not in sink
    assert (canary + "-rotated") not in sink
    for rec in app.log.records:
        assert "value" not in rec
        blob = json.dumps(rec)
        assert canary not in blob

    assert any(
        r["operation"] == "create" and r["name"] == "token" for r in app.log.records
    )
    assert any(r["operation"] == "get" and r["result"] == "ok" for r in app.log.records)

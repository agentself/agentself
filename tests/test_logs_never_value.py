"""Logs never contain secret values."""

from __future__ import annotations

import json
import uuid

from tests.support import setup_principal


def test_logs_never_contain_canary_value(app, monkeypatch):
    key = setup_principal(app.vault, "P", store="sops")
    app.keys["P"] = key
    app.bind(monkeypatch, "P")
    app.gateway.enroll("sops")
    canary = f"CANARY-{uuid.uuid4()}-SECRET"
    app.gateway.seal("token", canary)
    app.gateway.reveal("token")
    app.gateway.replace("token", canary + "-rotated")
    app.gateway.list()

    sink = app.log.rendered()
    assert canary not in sink
    assert (canary + "-rotated") not in sink
    for rec in app.log.records:
        assert "value" not in rec
        blob = json.dumps(rec)
        assert canary not in blob
        assert set(rec.keys()) == {"operation", "principal_id", "name", "result"}

    assert any(
        r["operation"] == "seal" and r["name"] == "token" for r in app.log.records
    )
    assert any(
        r["operation"] == "reveal" and r["result"] == "ok" for r in app.log.records
    )

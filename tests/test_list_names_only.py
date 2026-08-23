"""List never returns values."""

from __future__ import annotations

import uuid

from tests.support import setup_identity


def test_list_returns_names_never_values(app, monkeypatch):
    key = setup_identity(app.vault, "P", store="sops")
    app.keys["P"] = key
    app.bind(monkeypatch, "P")
    app.client.init("sops")
    canary = f"LISTCANARY-{uuid.uuid4()}"
    app.client.create("alpha", canary)
    app.client.create("beta", canary + "-two")
    names = app.client.list()
    assert names == ["alpha", "beta"]
    joined = " ".join(names)
    assert canary not in joined
    for item in names:
        assert item in {"alpha", "beta"}
        assert canary not in item

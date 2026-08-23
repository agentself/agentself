"""Bound identity Q cannot open P's secrets. Recipient mismatch stops before StoreAccess."""

from __future__ import annotations

import pytest

from agentself.internal.custody.errors import MissingSecret, Refused

from tests.support import init_identity, setup_identity


def _init_p_and_q(app, monkeypatch):
    init_identity(app, monkeypatch, "P")
    app.client.create("token", "p-secret")
    init_identity(app, monkeypatch, "Q")


def test_q_get_does_not_return_p_secret(app, monkeypatch):
    _init_p_and_q(app, monkeypatch)
    app.bind(monkeypatch, "Q")
    with pytest.raises(MissingSecret):
        app.client.get("token")
    app.bind(monkeypatch, "P")
    assert app.client.get("token") == "p-secret"


def test_q_own_list_does_not_include_p_names(app, monkeypatch):
    _init_p_and_q(app, monkeypatch)
    app.bind(monkeypatch, "Q")
    names = app.client.list()
    assert names == []


def test_recipient_mismatch_refuses_before_store_access(app, monkeypatch):
    app.keys["P"] = setup_identity(app.vault, "P", store="sops")
    app.keys["Q"] = setup_identity(app.vault, "Q", store="sops")
    app.bind(monkeypatch, "P")
    app.client.init("sops")
    app.client.create("token", "p-secret")
    monkeypatch.setenv("AGENTSELF_IDENTITY_ID", "P")
    monkeypatch.setenv("AGE_KEY_FILE", str(app.keys["Q"]))
    before = list(app.stores.calls)
    with pytest.raises(Refused):
        app.client.get("token")
    assert app.stores.calls == before

"""Unbound identity refuses with no backend calls; unknown identity refuses with no StoreAccess."""

from __future__ import annotations

import pytest

from agentself.internal.custody.errors import UnboundCaller, UnknownIdentity

from tests.support import setup_identity


def test_unbound_caller_refuses_with_no_ra_calls(app, monkeypatch):
    monkeypatch.delenv("AGENTSELF_IDENTITY_ID", raising=False)
    monkeypatch.delenv("AGE_KEY_FILE", raising=False)
    pa_before = list(app.identities.calls)
    sa_before = list(app.stores.calls)
    bind_before = list(app.stores.for_binding_calls)

    with pytest.raises(UnboundCaller):
        app.client.init("sops")
    with pytest.raises(UnboundCaller):
        app.client.list()
    with pytest.raises(UnboundCaller):
        app.client.identity()

    assert app.identities.calls == pa_before
    assert app.stores.calls == sa_before
    assert app.stores.for_binding_calls == bind_before
    assert app.mailboxes.calls == []
    assert app.wallets.calls == []
    assert any(r["result"] == "unbound" for r in app.log.records)


def test_unbound_missing_key_file_only(app, monkeypatch):
    monkeypatch.setenv("AGENTSELF_IDENTITY_ID", "P")
    monkeypatch.delenv("AGE_KEY_FILE", raising=False)
    pa_before = list(app.identities.calls)
    with pytest.raises(UnboundCaller):
        app.client.list()
    assert app.identities.calls == pa_before


def test_unknown_identity_refuses_with_no_store_access(app, monkeypatch):
    key = setup_identity(app.vault, "R", store="sops")
    app.keys["R"] = key
    app.bind(monkeypatch, "R")
    sa_before = list(app.stores.calls)
    bind_before = list(app.stores.for_binding_calls)

    with pytest.raises(UnknownIdentity):
        app.client.get("token")
    with pytest.raises(UnknownIdentity):
        app.client.list()
    with pytest.raises(UnknownIdentity):
        app.client.identity()

    assert app.stores.calls == sa_before
    assert app.stores.for_binding_calls == bind_before
    assert app.mailboxes.for_binding_calls == []
    assert app.wallets.for_binding_calls == []
    assert any(c[0] == "find" and c[1] == "R" for c in app.identities.calls)
    assert not any(c[0] == "init" for c in app.identities.calls)

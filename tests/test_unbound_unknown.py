"""Unbound caller refuses with no RA calls; unknown principal refuses with no StoreAccess."""

from __future__ import annotations

import pytest

from agentself.internal.custody.errors import UnboundCaller, UnknownPrincipal

from tests.support import setup_principal


def test_unbound_caller_refuses_with_no_ra_calls(app, monkeypatch):
    monkeypatch.delenv("AGENTSELF_IDENTITY_ID", raising=False)
    monkeypatch.delenv("AGE_KEY_FILE", raising=False)
    pa_before = list(app.principals.calls)
    sa_before = list(app.stores.calls)
    bind_before = list(app.stores.for_binding_calls)

    with pytest.raises(UnboundCaller):
        app.gateway.enroll("sops")
    with pytest.raises(UnboundCaller):
        app.gateway.seal("n", "v")
    with pytest.raises(UnboundCaller):
        app.gateway.reveal("n")
    with pytest.raises(UnboundCaller):
        app.gateway.replace("n", "v")
    with pytest.raises(UnboundCaller):
        app.gateway.list()
    with pytest.raises(UnboundCaller):
        app.gateway.email_list()
    with pytest.raises(UnboundCaller):
        app.gateway.wallet_address()
    with pytest.raises(UnboundCaller):
        app.gateway.identity()

    assert app.principals.calls == pa_before
    assert app.stores.calls == sa_before
    assert app.stores.for_binding_calls == bind_before
    assert app.mailboxes.calls == []
    assert app.wallets.calls == []
    assert any(r["result"] == "unbound" for r in app.log.records)


def test_unbound_missing_key_file_only(app, monkeypatch):
    monkeypatch.setenv("AGENTSELF_IDENTITY_ID", "P")
    monkeypatch.delenv("AGE_KEY_FILE", raising=False)
    pa_before = list(app.principals.calls)
    with pytest.raises(UnboundCaller):
        app.gateway.list()
    assert app.principals.calls == pa_before


def test_unknown_principal_refuses_with_no_store_access(app, monkeypatch):
    key = setup_principal(app.vault, "R", store="sops")
    app.keys["R"] = key
    app.bind(monkeypatch, "R")
    sa_before = list(app.stores.calls)
    bind_before = list(app.stores.for_binding_calls)

    with pytest.raises(UnknownPrincipal):
        app.gateway.reveal("token")
    with pytest.raises(UnknownPrincipal):
        app.gateway.seal("token", "v")
    with pytest.raises(UnknownPrincipal):
        app.gateway.replace("token", "v")
    with pytest.raises(UnknownPrincipal):
        app.gateway.list()
    with pytest.raises(UnknownPrincipal):
        app.gateway.email_list()
    with pytest.raises(UnknownPrincipal):
        app.gateway.wallet_address()
    with pytest.raises(UnknownPrincipal):
        app.gateway.identity()

    assert app.stores.calls == sa_before
    assert app.stores.for_binding_calls == bind_before
    assert app.mailboxes.for_binding_calls == []
    assert app.wallets.for_binding_calls == []
    assert any(c[0] == "find" and c[1] == "R" for c in app.principals.calls)
    assert not any(c[0] == "enroll" for c in app.principals.calls)

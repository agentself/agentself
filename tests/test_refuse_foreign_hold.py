"""RefuseForeignHold — Q / Orchestrator cannot open P's hold. Stop before StoreAccess."""

from __future__ import annotations

import pytest

from agentself.internal.custody.errors import Refused

from tests.support import setup_principal


def _enroll_p_and_q(app, monkeypatch):
    app.keys["P"] = setup_principal(app.vault, "P", store="sops")
    app.keys["Q"] = setup_principal(app.vault, "Q", store="sops")
    app.keys["Orchestrator"] = setup_principal(app.vault, "Orchestrator", store="sops")
    app.bind(monkeypatch, "P")
    app.gateway.enroll("sops")
    app.gateway.seal("token", "p-secret")
    app.bind(monkeypatch, "Q")
    app.gateway.enroll("sops")
    app.bind(monkeypatch, "Orchestrator")
    app.gateway.enroll("sops")


@pytest.mark.parametrize("as_who", ["Q", "Orchestrator"])
@pytest.mark.parametrize("verb", ["reveal", "list", "replace", "seal"])
def test_refuse_foreign_hold_before_store_access(app, monkeypatch, as_who, verb):
    _enroll_p_and_q(app, monkeypatch)
    app.bind(monkeypatch, as_who)
    before = list(app.stores.calls)
    before_bindings = list(app.stores.for_binding_calls)

    if verb == "reveal":
        with pytest.raises(Refused):
            app.gateway.reveal("token", hold_owner="P")
    elif verb == "list":
        with pytest.raises(Refused):
            app.gateway.list(hold_owner="P")
    elif verb == "replace":
        with pytest.raises(Refused):
            app.gateway.replace("token", "hijack", hold_owner="P")
    else:
        with pytest.raises(Refused):
            app.gateway.seal("other", "x", hold_owner="P")

    assert app.stores.calls == before
    assert app.stores.for_binding_calls == before_bindings


def test_foreign_list_does_not_leak_p_names(app, monkeypatch):
    _enroll_p_and_q(app, monkeypatch)
    app.bind(monkeypatch, "Q")
    before = list(app.stores.calls)
    with pytest.raises(Refused):
        names = app.gateway.list(hold_owner="P")
        assert "token" not in names
    assert app.stores.calls == before
    own = app.gateway.list()
    assert "token" not in own


def test_q_own_list_does_not_include_p_names(app, monkeypatch):
    _enroll_p_and_q(app, monkeypatch)
    app.bind(monkeypatch, "Q")
    names = app.gateway.list()
    assert names == []


def test_recipient_mismatch_refuses_before_store_access(app, monkeypatch):
    app.keys["P"] = setup_principal(app.vault, "P", store="sops")
    app.keys["Q"] = setup_principal(app.vault, "Q", store="sops")
    app.bind(monkeypatch, "P")
    app.gateway.enroll("sops")
    app.gateway.seal("token", "p-secret")
    monkeypatch.setenv("AGENTSELF_IDENTITY_ID", "P")
    monkeypatch.setenv("AGE_KEY_FILE", str(app.keys["Q"]))
    before = list(app.stores.calls)
    with pytest.raises(Refused):
        app.gateway.reveal("token")
    assert app.stores.calls == before

"""Replace on missing name fails and does not Seal."""

from __future__ import annotations

import pytest

from agentself.internal.custody.errors import MissingHoldName

from tests.support import setup_principal


@pytest.mark.parametrize("store", ["sops", "pass"])
def test_replace_missing_does_not_seal(app, monkeypatch, store):
    key = setup_principal(app.vault, "P", store=store)
    app.keys["P"] = key
    app.bind(monkeypatch, "P")
    app.gateway.enroll(store)

    with pytest.raises(MissingHoldName):
        app.gateway.replace("ghost", "should-not-land")

    with pytest.raises(MissingHoldName):
        app.gateway.reveal("ghost")
    assert "ghost" not in app.gateway.list()

    store_calls = [c for c in app.stores.calls if c[0] == "seal"]
    assert store_calls == []
    replace_calls = [c for c in app.stores.calls if c[0] == "replace"]
    assert replace_calls == [("replace", "P", "ghost")]

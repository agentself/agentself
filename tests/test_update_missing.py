"""Update on a missing name fails and does not create."""

from __future__ import annotations

import pytest

from agentself.internal.custody.errors import MissingSecret

from tests.support import init_identity


@pytest.mark.parametrize("store", ["sops", "pass"])
def test_update_missing_does_not_create(app, monkeypatch, store):
    init_identity(app, monkeypatch, store=store)

    with pytest.raises(MissingSecret):
        app.client.update("ghost", "should-not-land")

    with pytest.raises(MissingSecret):
        app.client.get("ghost")
    assert "ghost" not in app.client.list()

    store_calls = [c for c in app.stores.calls if c[0] == "create"]
    assert store_calls == []
    update_calls = [c for c in app.stores.calls if c[0] == "update"]
    assert update_calls == [("update", "P", "ghost")]

"""A no-tool store double uses Client/Manager without parser or Manager changes."""

from __future__ import annotations

import pytest

from agentself.backends.store.contract import StoreResourceError
from agentself.backends.store.factory import StoreAccessFactory
from agentself.bind import public_recipient
from agentself.compose import compose
from agentself.host import CHANNELS, Bind, Channel
from agentself.internal.custody.errors import StoreFailure
from agentself.internal.files import identity_home
from agentself.internal.types import BoundCaller

from tests.support import build_app, init_identity, setup_identity
from tests.synthetic_store import MemoryStoreAccess


def _patch_memory_catalog(monkeypatch) -> None:
    store = CHANNELS["store"]
    memory = Bind(
        "memory",
        "in-memory test store",
        live=False,
        verbs=store.binds[0].verbs,
        tools=(),
        installable_tools=(),
    )
    monkeypatch.setitem(
        CHANNELS,
        "store",
        Channel(
            name=store.name,
            env=store.env,
            config_key=store.config_key,
            default=store.default,
            binds=(*store.binds, memory),
            note=store.note,
        ),
    )


def test_init_retries_prepare_after_failed_prepare(vault, monkeypatch):
    app = build_app(vault)
    app.keys["P"] = setup_identity(app.vault, "P")
    app.bind(monkeypatch, "P")
    n = {"c": 0}
    real = MemoryStoreAccess.prepare

    def flaky(self, identity_id):
        n["c"] += 1
        if n["c"] == 1:
            raise StoreResourceError("gpg keygen failed: socket name is too long")
        return real(self, identity_id)

    monkeypatch.setattr(MemoryStoreAccess, "prepare", flaky)
    with pytest.raises(StoreFailure, match="socket name is too long"):
        app.client.init("memory")
    assert n["c"] == 1
    view = app.client.init("memory")
    assert view["id"] == "P"
    assert n["c"] == 2


def test_memory_store_create_get_via_client(vault, monkeypatch):
    app = build_app(vault)
    init_identity(app, monkeypatch, store="memory")
    prepares = [call for call in app.stores.calls if call[0] == "prepare"]
    assert prepares == [("prepare", "P", None)]
    app.client.create("notes", "only-I-can-read")
    assert app.client.get("notes") == "only-I-can-read"
    assert "notes" in app.client.list()
    assert not (identity_home(vault, "P") / "gnupg").exists()
    assert not (identity_home(vault, "P") / "password-store").exists()


def test_compose_catalog_injection_accepts_memory(tmp_path, monkeypatch):
    _patch_memory_catalog(monkeypatch)
    shared = MemoryStoreAccess()

    class Wrapped(StoreAccessFactory):
        def for_binding(self, binding: str):
            if binding == "memory":
                return shared
            return super().for_binding(binding)

    monkeypatch.setattr("agentself.compose.StoreAccessFactory", Wrapped)
    vault = tmp_path / "vault"
    key = setup_identity(vault, "agent")
    recipient = public_recipient(str(key))
    monkeypatch.setenv("AGENTSELF_IDENTITY_DIR", str(vault))
    monkeypatch.setenv("AGENTSELF_IDENTITY_ID", "agent")
    monkeypatch.setenv("AGE_KEY_FILE", str(key))
    client = compose(vault, bind=lambda: BoundCaller("agent", recipient))
    client.init("memory")
    assert shared.prepare_calls == 1
    assert shared.required_tools() == ()
    client.create("notes", "from-compose")
    assert client.get("notes") == "from-compose"
    assert not (identity_home(vault, "agent") / "gnupg").exists()

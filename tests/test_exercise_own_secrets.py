"""A bound identity can create, get, update, and list its own secrets."""

from __future__ import annotations

import pytest

from agentself.internal.files import identity_home, secrets_home

from tests.support import setup_identity


@pytest.mark.parametrize("store", ["sops", "pass"])
def test_exercise_own_secrets(app, monkeypatch, store):
    key = setup_identity(app.vault, "P", store=store)
    app.keys["P"] = key
    app.bind(monkeypatch, "P")

    view = app.client.init(store)
    assert view["id"] == "P"
    assert view["recipient"].startswith("age1")
    assert "AGE-SECRET-KEY" not in view["recipient"]
    assert set(view.keys()) == {"id", "recipient"}

    app.client.create("token", "v1")
    assert app.client.get("token") == "v1"

    app.client.update("token", "v2")
    assert app.client.get("token") == "v2"

    names = app.client.list()
    assert names == ["token"]
    assert "v1" not in names
    assert "v2" not in names


def test_exercise_own_secrets_sops_writes_ciphertext_not_plaintext(app, monkeypatch):
    key = setup_identity(app.vault, "P", store="sops")
    app.keys["P"] = key
    app.bind(monkeypatch, "P")
    app.client.init("sops")
    secret = "plaintext-must-not-hit-disk-sops"
    app.client.create("api", secret)
    hold = secrets_home(app.vault, "P")
    files = list(hold.glob("*.sops"))
    assert files
    blob = files[0].read_bytes()
    assert secret.encode() not in blob
    assert (identity_home(app.vault, "P") / "password-store").exists() is False


def test_exercise_own_secrets_pass_is_real_pass_not_sops_tree(app, monkeypatch):
    key = setup_identity(app.vault, "P", store="pass")
    app.keys["P"] = key
    app.bind(monkeypatch, "P")
    app.client.init("pass")
    secret = "plaintext-must-not-hit-disk-pass"
    app.client.create("api", secret)
    store_dir = identity_home(app.vault, "P") / "password-store"
    gpg_files = list(store_dir.glob("*.gpg"))
    assert gpg_files, "pass store must write .gpg files"
    assert secret.encode() not in gpg_files[0].read_bytes()
    hold = secrets_home(app.vault, "P")
    assert not list(hold.glob("*.sops")) if hold.exists() else True
    gpg_home = identity_home(app.vault, "P") / "gnupg"
    assert gpg_home.is_dir()
    assert (store_dir / ".gpg-id").is_file()


def test_init_twice_keeps_first_store_binding(app, monkeypatch):
    key = setup_identity(app.vault, "P", store="sops")
    app.keys["P"] = key
    app.bind(monkeypatch, "P")
    first = app.client.init("sops")
    again = app.client.init("pass")
    assert first == again
    identity = app.identities.find("P")
    assert identity is not None
    assert identity.store_binding == "sops"

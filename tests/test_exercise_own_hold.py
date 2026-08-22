"""ExerciseOwnHold — principal P Enrolls, Seals, Reveals, Replaces, Lists its own hold."""

from __future__ import annotations

import pytest

from agentself.internal.files import identity_home, secrets_home

from tests.support import setup_principal


@pytest.mark.parametrize("store", ["sops", "pass"])
def test_exercise_own_hold(app, monkeypatch, store):
    key = setup_principal(app.vault, "P", store=store)
    app.keys["P"] = key
    app.bind(monkeypatch, "P")

    view = app.gateway.enroll(store)
    assert view["id"] == "P"
    assert view["recipient"].startswith("age1")
    assert "AGE-SECRET-KEY" not in view["recipient"]
    assert set(view.keys()) == {"id", "recipient"}

    app.gateway.seal("token", "v1")
    assert app.gateway.reveal("token") == "v1"

    app.gateway.replace("token", "v2")
    assert app.gateway.reveal("token") == "v2"

    names = app.gateway.list()
    assert names == ["token"]
    assert "v1" not in names
    assert "v2" not in names


def test_exercise_own_hold_sops_writes_ciphertext_not_plaintext(app, monkeypatch):
    key = setup_principal(app.vault, "P", store="sops")
    app.keys["P"] = key
    app.bind(monkeypatch, "P")
    app.gateway.enroll("sops")
    secret = "plaintext-must-not-hit-disk-sops"
    app.gateway.seal("api", secret)
    hold = secrets_home(app.vault, "P")
    files = list(hold.glob("*.sops"))
    assert files
    blob = files[0].read_bytes()
    assert secret.encode() not in blob
    assert (identity_home(app.vault, "P") / "password-store").exists() is False


def test_exercise_own_hold_pass_is_real_pass_not_sops_tree(app, monkeypatch):
    key = setup_principal(app.vault, "P", store="pass")
    app.keys["P"] = key
    app.bind(monkeypatch, "P")
    app.gateway.enroll("pass")
    secret = "plaintext-must-not-hit-disk-pass"
    app.gateway.seal("api", secret)
    store_dir = identity_home(app.vault, "P") / "password-store"
    gpg_files = list(store_dir.glob("*.gpg"))
    assert gpg_files, "pass store must write .gpg files"
    assert secret.encode() not in gpg_files[0].read_bytes()
    hold = secrets_home(app.vault, "P")
    assert not list(hold.glob("*.sops")) if hold.exists() else True
    gpg_home = identity_home(app.vault, "P") / "gnupg"
    assert gpg_home.is_dir()
    assert (store_dir / ".gpg-id").is_file()


def test_enroll_twice_keeps_first_store_binding(app, monkeypatch):
    key = setup_principal(app.vault, "P", store="sops")
    app.keys["P"] = key
    app.bind(monkeypatch, "P")
    first = app.gateway.enroll("sops")
    again = app.gateway.enroll("pass")
    assert first == again
    principal = app.principals.find("P")
    assert principal is not None
    assert principal.store_binding == "sops"

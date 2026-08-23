"""A non-EVM wallet double uses Client/Manager without parser or Client changes."""

from __future__ import annotations

import json

import pytest

from agentself.cli.app import main
from agentself.internal.custody.errors import CannotSend

from tests.support import apply_cli_env, build_app, cli_env, init_identity, run_cli
from tests.synthetic_wallet import SyntheticWalletAccess


def test_omitted_asset_resolves_to_backend_default(vault, monkeypatch):
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    assert app.client.wallet_send("dest", "1") == "NOTE"


def test_manager_does_not_create_or_get_wallet_key(vault, monkeypatch):
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    app.client.wallet_address()
    app.client.wallet_send("dest", "1")
    named = [
        call[2]
        for call in app.stores.calls
        if call[0] in {"create", "get", "update"} and len(call) > 2
    ]
    assert "wallet.key" not in named


def test_unsupported_asset_is_typed_without_chain_strings(vault, monkeypatch):
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    with pytest.raises(CannotSend) as caught:
        app.client.wallet_send("dest", "1", "USD")
    assert caught.value.reason == "unsupported_asset"
    blob = str(caught.value)
    assert "ETH" not in blob
    assert "USDC" not in blob


def test_verify_scheme_is_backend_reported(vault, monkeypatch):
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    token = app.client.wallet_authorize("hello")
    checked = app.client.wallet_verify("hello", token)
    assert checked["scheme"] == "ed25519"
    assert checked["valid"] is True


def test_cli_unsupported_asset_maps_without_chain_strings(
    tmp_path, monkeypatch, capsys
):
    env = cli_env(tmp_path / "vault")
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr
    apply_cli_env(monkeypatch, env)
    monkeypatch.setattr(
        "agentself.compose.WalletAccessFactory.for_binding",
        lambda self, binding: SyntheticWalletAccess(),
    )
    code = main(["--json", "wallet", "send", "dest", "1", "USD"])
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    assert code == 2, blob
    assert "ETH" not in blob
    assert "USDC" not in blob
    data = json.loads(captured.out)
    assert data["ok"] is False
    assert data["error"] == "refused"
    assert data["reason"] == "unsupported_asset"

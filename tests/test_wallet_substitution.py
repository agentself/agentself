"""A non-EVM wallet double uses Client/Manager without parser or Client changes."""

from __future__ import annotations

import json

import pytest

from agentself.backends.wallet.contract import WalletMaterial
from agentself.cli.app import main
from agentself.internal.custody.errors import CannotSend

from tests.fiat_wallet import FiatWalletAccess
from tests.support import (
    apply_cli_env,
    build_app,
    cli_env,
    init_identity,
    run_cli,
    value_file,
)
from tests.synthetic_wallet import MATERIAL_NAME, SyntheticWalletAccess


def test_omitted_asset_resolves_to_backend_default(vault, monkeypatch):
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    assert app.client.wallet_send("dest", "1") == {"asset": "NOTE"}


def test_cli_test_uses_synthetic_backend_validation(vault, monkeypatch, capsys):
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    monkeypatch.setattr(
        "agentself.cli.commands.wallet.client", lambda _vault: app.client
    )
    monkeypatch.setattr(
        "agentself.internal.host_tools.ensure_host_tools", lambda fetch=False: None
    )

    assert main(["--json", "wallet", "send", "dest", "1", "--test"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["test"] is True
    assert data["asset"] == "NOTE"
    assert ("validate_send",) in app.wallets.instances[-1].calls
    assert ("send",) not in app.wallets.instances[-1].calls


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


def test_manager_owns_material_create_bind_and_reuse(vault, monkeypatch):
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)

    app.client.wallet_address()
    first = app.wallets.instances[-1].calls
    assert ("create_material",) in first
    assert ("bind_material", "synthetic-note-seed") in first
    assert first[-1] == ("address",)

    app.client.wallet_address()
    second = app.wallets.instances[-1].calls
    assert ("create_material",) not in second
    assert ("bind_material", "synthetic-note-seed") in second
    material_calls = [
        call[0]
        for call in app.stores.calls
        if len(call) > 2 and call[2] == MATERIAL_NAME
    ]
    assert material_calls == ["create", "get"]


def test_manager_reuses_existing_material_without_overwrite(vault, monkeypatch):
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    assert app.client.create(MATERIAL_NAME, "preexisting") is False

    app.client.wallet_address()
    calls = app.wallets.instances[-1].calls
    assert ("create_material",) not in calls
    assert ("bind_material", "preexisting") in calls
    material_writes = [
        call[0]
        for call in app.stores.calls
        if len(call) > 2 and call[2] == MATERIAL_NAME
    ]
    assert material_writes == ["create", "get"]


def test_wallet_material_diagnose_uses_declared_name(vault, monkeypatch):
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)

    assert app.client.wallet_material_status() == {
        "ready": False,
        "missing": MATERIAL_NAME,
    }
    app.client.wallet_address()
    assert app.client.wallet_material_status() == {"ready": True, "missing": None}


def test_manager_retains_protection_for_multiple_wallet_material_names(
    vault, monkeypatch
):
    class AlternateWalletAccess(SyntheticWalletAccess):
        def required_material(self):
            return WalletMaterial(name="other.seed")

        def create_material(self) -> str:
            return "alternate-seed"

    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    app.client.wallet_address()
    monkeypatch.setattr(
        app.wallets.inner,
        "for_binding",
        lambda binding: AlternateWalletAccess(),
    )
    app.client.wallet_address()

    assert app.client.protected_secret_names() == [
        "note.seed",
        "other.seed",
        "wallet.key",
    ]


def test_synthetic_send_refuses_details_without_chain_strings(vault, monkeypatch):
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    with pytest.raises(CannotSend) as caught:
        app.client.wallet_send("dest", "1", details='{"allow": true}')
    assert caught.value.reason == "unsupported_details"
    blob = str(caught.value)
    assert "ETH" not in blob
    assert "USDC" not in blob
    assert "approve" not in blob.lower()


def test_fiat_wallet_send_details_and_named_balance(vault, monkeypatch):
    fiat = FiatWalletAccess()
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    monkeypatch.setattr(app.wallets.inner, "for_binding", lambda binding: fiat)

    assert app.client.wallet_send("merchant", "10") == {"asset": "USD"}
    assert fiat.transfers == [("merchant", "10", "USD")]

    assert app.client.wallet_send("merchant", "5", details='{"allow": true}') == {
        "asset": "USD"
    }
    assert fiat.allowances == [("merchant", "5", "USD")]

    assert app.client.wallet_send(
        "merchant", "3", "EUR", details='{"memo": "invoice-9"}'
    ) == {"asset": "EUR"}
    assert fiat.payments == [("merchant", "3", "EUR", "invoice-9")]

    default = app.client.wallet_balance()
    assert default["asset"] == "USD"
    assert default["amount"] == "100"
    assert "ETH" not in json.dumps(default)
    assert "USDC" not in json.dumps(default)
    named = app.client.wallet_balance("EUR")
    assert named == {"asset": "EUR", "amount": "25", "address": "acct.1"}


def test_cli_fiat_send_file_and_balance_stay_generic(tmp_path, monkeypatch, capsys):
    env = cli_env(tmp_path / "vault")
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr
    apply_cli_env(monkeypatch, env)
    fiat = FiatWalletAccess()
    monkeypatch.setattr(
        "agentself.compose.WalletAccessFactory.for_binding",
        lambda self, binding: fiat,
    )
    allow = value_file(tmp_path, '{"allow": true}\n', "allow.json")
    code = main(["--json", "wallet", "send", "merchant", "8", "--file", allow])
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    data = json.loads(captured.out)
    assert data["ok"] is True
    assert data["to"] == "merchant"
    assert data["asset"] == "USD"
    assert data["details_sha256"]
    assert "authorization" not in data
    assert "ETH" not in captured.out
    assert "USDC" not in captured.out
    assert fiat.allowances == [("merchant", "8", "USD")]

    code = main(["--json", "wallet", "balance", "EUR"])
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    named = json.loads(captured.out)
    assert named["asset"] == "EUR"
    assert named["amount"] == "25"
    assert "ETH" not in captured.out
    assert "USDC" not in captured.out


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


def test_cli_protects_declared_material_for_list_export_and_delete(
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

    assert main(["--json", "wallet", "address"]) == 0
    capsys.readouterr()

    assert main(["--json", "secret", "list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert MATERIAL_NAME in listed["names"]
    assert MATERIAL_NAME in listed["protected"]

    assert main(["--json", "secret", "get", MATERIAL_NAME]) == 2
    refused = json.loads(capsys.readouterr().out)
    assert refused["error"] == "refused"
    assert MATERIAL_NAME in refused["reason"]

    assert main(["--json", "secret", "get", MATERIAL_NAME, "--unsafe"]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["value"] == "synthetic-note-seed"

    assert main(["--json", "secret", "delete", MATERIAL_NAME]) == 2
    deleted = json.loads(capsys.readouterr().out)
    assert deleted["error"] == "refused"
    assert MATERIAL_NAME in deleted["reason"]

    planted = value_file(tmp_path, "replaced-seed", "seed.txt")
    assert main(["--json", "secret", "update", MATERIAL_NAME, "--file", planted]) == 2
    updated = json.loads(capsys.readouterr().out)
    assert updated["error"] == "refused"
    assert MATERIAL_NAME in updated["reason"]
    assert (
        main(
            [
                "--json",
                "secret",
                "update",
                MATERIAL_NAME,
                "--unsafe",
                "--file",
                planted,
            ]
        )
        == 0
    )
    capsys.readouterr()

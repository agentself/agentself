"""Wallet address is stable; authorization is verifiable; key hidden; send fails closed."""

from __future__ import annotations

import json
import re

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from agentself.backends.wallet import contract as wallet_contract
from agentself.backends.wallet.base.errors import NoEthForGas
from agentself.backends.wallet.contract import (
    CannotSend as WalletCannotSend,
)
from agentself.backends.wallet.contract import (
    WalletError,
)
from agentself.backends.wallet.factory import WalletAccessFactory
from agentself.cli.app import main
from agentself.internal.custody.errors import (
    CannotSend,
    ChannelFailure,
    NoGas,
)
from agentself.internal.log import MemoryLog

from tests.support import (
    MockRpc,
    build_app,
    init_identity,
)

_HEX64 = re.compile(r"\b[0-9a-fA-F]{64}\b")


def test_wallet_address_stable_sign_verifiable_key_hidden(app, monkeypatch):
    init_identity(app, monkeypatch)
    first = app.client.wallet_address()
    second = app.client.wallet_address()
    assert first == second
    assert first.startswith("0x")
    assert len(first) == 42

    message = "agentself-sign"
    sig = app.client.wallet_authorize(message)
    recovered = Account.recover_message(encode_defunct(text=message), signature=sig)
    assert recovered == first

    key = app.client.get("wallet.key")
    assert key.startswith("0x") or len(key) >= 64
    assert key not in first
    assert key not in sig
    _assert_key_hidden(app, key)
    assert "AGE-SECRET-KEY" not in app.log.rendered()


def test_wallet_send_fails_closed_without_eth(app, monkeypatch):
    init_identity(app, monkeypatch)
    app.client.wallet_address()
    with pytest.raises(NoGas, match="gas") as caught:
        app.client.wallet_send("0x" + "11" * 20, "1")
    assert caught.value.reason == "no_gas"
    assert "ETH" not in str(caught.value)
    assert "USDC" not in str(caught.value)


def test_base_send_without_eth_raises_backend_error_not_contract_type():
    from agentself.internal.eoa import generate_secp256k1

    wallet = WalletAccessFactory(MemoryLog(), rpc=MockRpc(eth_wei=0)).for_binding(
        "base"
    )
    wallet.bind_material(generate_secp256k1())
    with pytest.raises(NoEthForGas, match="need ETH for gas"):
        wallet.send("P", "0x" + "11" * 20, "1", "USDC")
    assert not hasattr(wallet_contract, "NoEthForGas")


def test_wallet_ops_use_bound_identity(app, monkeypatch):
    init_identity(app, monkeypatch, "P")
    p_addr = app.client.wallet_address()
    init_identity(app, monkeypatch, "Q")
    q_addr = app.client.wallet_address()
    assert q_addr != p_addr
    assert q_addr.startswith("0x")


def _assert_describe_has_no_key(view: dict[str, object]) -> None:
    assert "key" not in view
    assert "key_hex" not in view
    blob = json.dumps(view)
    assert "AGE-SECRET-KEY" not in blob
    assert _HEX64.search(blob) is None


def test_describe_never_contains_a_key():
    log = MemoryLog()
    factory = WalletAccessFactory(log, rpc=MockRpc())
    base = factory.for_binding("base")
    from agentself.internal.eoa import generate_secp256k1

    base.bind_material(generate_secp256k1())
    _assert_describe_has_no_key(base.describe("P"))


def _assert_key_hidden(app, key: str) -> None:
    bare = key.lower().removeprefix("0x")
    sink = app.log.rendered()
    assert key not in sink
    assert bare not in sink.lower()
    for rec in app.log.records:
        blob = json.dumps(rec)
        assert key not in blob
        assert bare not in blob.lower()


def test_wallet_balance_includes_native_gas(vault, monkeypatch):
    rpc = MockRpc(eth_wei=10**18, usdc_raw=1_500_000)
    app = build_app(vault, rpc=rpc)
    init_identity(app, monkeypatch)
    bal = app.client.wallet_balance()
    assert bal["asset"] == "USDC"
    assert bal["amount"] == "1.5"
    assert bal["gas_asset"] == "ETH"
    assert bal["gas_raw"] == "1000000000000000000"
    assert bal["gas_amount"] == "1"
    assert "e" not in bal["gas_amount"].lower()
    assert any(c[0] == "eth_call" for c in rpc.calls)
    assert any(c[0] == "eth_getBalance" for c in rpc.calls)


def test_wallet_balance_zero_eth_is_success(vault, monkeypatch):
    rpc = MockRpc(eth_wei=0, usdc_raw=0)
    app = build_app(vault, rpc=rpc)
    init_identity(app, monkeypatch)
    bal = app.client.wallet_balance()
    assert bal["asset"] == "USDC"
    assert bal["amount"] == "0"
    assert bal["gas_asset"] == "ETH"
    assert bal["gas_raw"] == "0"
    assert bal["gas_amount"] == "0"


def test_wallet_send_broadcasts_when_funded(vault, monkeypatch):
    rpc = MockRpc(eth_wei=10**18, usdc_raw=2_000_000)
    app = build_app(vault, rpc=rpc)
    init_identity(app, monkeypatch)
    app.client.wallet_send("0x" + "11" * 20, "1")
    assert rpc.broadcast
    assert any(c[0] == "eth_sendRawTransaction" for c in rpc.calls)
    key = app.client.get("wallet.key")
    _assert_key_hidden(app, key)


@pytest.mark.parametrize("operation", ["validate_send", "send"])
def test_chain_send_requires_eth_to_cover_estimated_gas(operation):
    from agentself.internal.eoa import generate_secp256k1

    rpc = MockRpc(eth_wei=10**14 - 1, usdc_raw=2_000_000)
    wallet = WalletAccessFactory(MemoryLog(), rpc=rpc).for_binding("base")
    wallet.bind_material(generate_secp256k1())

    with pytest.raises(WalletCannotSend) as caught:
        getattr(wallet, operation)("P", "0x" + "11" * 20, "1", "USDC")

    assert caught.value.reason == "no_gas"
    assert not rpc.broadcast
    assert [method for method, _ in rpc.calls].count("eth_gasPrice") == 1
    assert [method for method, _ in rpc.calls].count("eth_estimateGas") == 1
    assert not any(method == "eth_getTransactionCount" for method, _ in rpc.calls)


def test_wallet_send_refuses_without_usdc(vault, monkeypatch):
    from agentself.internal.eoa import generate_secp256k1

    rpc = MockRpc(eth_wei=10**18, usdc_raw=0)
    wallet = WalletAccessFactory(MemoryLog(), rpc=rpc).for_binding("base")
    wallet.bind_material(generate_secp256k1())
    with pytest.raises(WalletCannotSend) as adapter:
        wallet.send("P", "0x" + "11" * 20, "1", "USDC")
    assert adapter.value.reason == "insufficient_asset"
    assert not rpc.broadcast
    app = build_app(vault, rpc=MockRpc(eth_wei=10**18, usdc_raw=0))
    init_identity(app, monkeypatch)
    with pytest.raises(CannotSend) as caught:
        app.client.wallet_send("0x" + "11" * 20, "1")
    assert caught.value.reason == "insufficient_asset"
    assert "USDC" not in str(caught.value)
    assert not app.rpc.broadcast


def test_wallet_send_wrong_asset_names_usdc_before_eth_check():
    from agentself.internal.eoa import generate_secp256k1

    wallet = WalletAccessFactory(MemoryLog(), rpc=MockRpc(eth_wei=0)).for_binding(
        "base"
    )
    wallet.bind_material(generate_secp256k1())
    with pytest.raises(WalletCannotSend) as eth_asset:
        wallet.send("P", "0x" + "11" * 20, "1", "ETH")
    assert eth_asset.value.reason == "unsupported_asset"
    assert "need ETH for gas" not in str(eth_asset.value)
    with pytest.raises(WalletCannotSend) as lower:
        wallet.send("P", "0x" + "11" * 20, "1", "usdc")
    assert lower.value.reason == "unsupported_asset"
    assert "need ETH for gas" not in str(lower.value)


def test_wallet_send_rpc_boom_is_channel_rpc(vault, monkeypatch):
    class BoomRpc:
        def request(self, method: str, params: list[object]) -> object:
            raise WalletError("rpc failed")

    app = build_app(vault, rpc=BoomRpc())
    init_identity(app, monkeypatch)
    with pytest.raises(ChannelFailure) as caught:
        app.client.wallet_send("0x" + "11" * 20, "1")
    assert caught.value.reason == "rpc"


def test_chain_wallet_send_missing_key():
    wallet = WalletAccessFactory(MemoryLog(), rpc=MockRpc(eth_wei=10**18)).for_binding(
        "base"
    )
    with pytest.raises(WalletError, match="missing key"):
        wallet.send("P", "0x" + "11" * 20, "1", "USDC")


def test_wallet_send_invalid_amount_names_usdc(vault, monkeypatch):
    rpc = MockRpc(eth_wei=10**18, usdc_raw=2_000_000)
    app = build_app(vault, rpc=rpc)
    init_identity(app, monkeypatch)
    with pytest.raises(CannotSend) as caught:
        app.client.wallet_send("0x" + "11" * 20, "-1")
    assert caught.value.reason == "invalid_amount"
    assert "USDC" not in str(caught.value)
    assert not rpc.broadcast


@pytest.mark.parametrize(
    ("to", "amount", "asset", "rpc", "reason"),
    [
        (
            "not-an-address",
            "1",
            "",
            MockRpc(eth_wei=10**18, usdc_raw=2_000_000),
            "invalid_destination",
        ),
        (
            "0x" + "11" * 20,
            "not-an-amount",
            "",
            MockRpc(eth_wei=10**18, usdc_raw=2_000_000),
            "invalid_amount",
        ),
        (
            "0x" + "11" * 20,
            "-1",
            "",
            MockRpc(eth_wei=10**18, usdc_raw=2_000_000),
            "invalid_amount",
        ),
        (
            "0x" + "11" * 20,
            "1",
            "ETH",
            MockRpc(eth_wei=10**18, usdc_raw=2_000_000),
            "unsupported_asset",
        ),
        (
            "0x" + "11" * 20,
            "1",
            "",
            MockRpc(eth_wei=10**18, usdc_raw=0),
            "insufficient_asset",
        ),
        ("0x" + "11" * 20, "1", "", MockRpc(eth_wei=0, usdc_raw=2_000_000), "no_gas"),
    ],
)
def test_cli_wallet_send_test_reuses_backend_validation(
    vault, monkeypatch, capsys, to, amount, asset, rpc, reason
):
    app = build_app(vault, rpc=rpc)
    init_identity(app, monkeypatch)
    monkeypatch.setattr(
        "agentself.cli.commands.wallet.client", lambda _vault: app.client
    )
    monkeypatch.setattr(
        "agentself.internal.host_tools.ensure_host_tools", lambda fetch=False: None
    )

    argv = ["--json", "wallet", "send", to, amount]
    if asset:
        argv.append(asset)
    argv.append("--test")
    assert main(argv) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert data["reason"] == reason
    assert not rpc.broadcast
    assert not (vault / "identities" / "P" / "wallet" / "pending-send.json").exists()


def test_cli_wallet_send_test_returns_valid_plan_without_state_changes(
    vault, monkeypatch, capsys
):
    rpc = MockRpc(eth_wei=10**18, usdc_raw=2_000_000)
    app = build_app(vault, rpc=rpc)
    init_identity(app, monkeypatch)
    monkeypatch.setattr(
        "agentself.cli.commands.wallet.client", lambda _vault: app.client
    )
    monkeypatch.setattr(
        "agentself.internal.host_tools.ensure_host_tools", lambda fetch=False: None
    )

    assert main(["--json", "wallet", "send", "0x" + "11" * 20, "1", "--test"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["test"] is True
    assert data["asset"] == "USDC"
    assert data["amount"] == "1"
    assert not rpc.broadcast
    assert not any(method == "eth_sendRawTransaction" for method, _ in rpc.calls)
    assert not any(method == "eth_getTransactionCount" for method, _ in rpc.calls)
    assert not (vault / "identities" / "P" / "wallet" / "pending-send.json").exists()

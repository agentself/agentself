"""Wallet Address stable; Sign verifiable; key hidden; mocked balance; send fail-closed."""

from __future__ import annotations

import inspect
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
    WalletAccess,
    WalletError,
)
from agentself.backends.wallet.factory import WalletAccessFactory
from agentself.internal.custody.errors import (
    CannotSend,
    ChannelFailure,
    NoGas,
    Refused,
)
from agentself.internal.log import MemoryLog

from tests.support import (
    PROJECT_ROOT,
    MockRpc,
    build_app,
    enroll_principal,
    setup_principal,
)

_HEX64 = re.compile(r"\b[0-9a-fA-F]{64}\b")
_CONTRACT = PROJECT_ROOT / "agentself" / "backends" / "wallet" / "contract.py"


def test_wallet_address_stable_sign_verifiable_key_hidden(app, monkeypatch):
    enroll_principal(app, monkeypatch)
    first = app.gateway.wallet_address()
    second = app.gateway.wallet_address()
    assert first == second
    assert first.startswith("0x")
    assert len(first) == 42

    message = "agentself-sign"
    sig = app.gateway.wallet_sign(message)
    recovered = Account.recover_message(encode_defunct(text=message), signature=sig)
    assert recovered == first

    key = app.gateway.reveal("wallet.key")
    assert key.startswith("0x") or len(key) >= 64
    assert key not in first
    assert key not in sig
    bare = key.lower().removeprefix("0x")
    sink = app.log.rendered()
    assert key not in sink
    assert bare not in sink.lower()
    assert "AGE-SECRET-KEY" not in sink
    for rec in app.log.records:
        blob = json.dumps(rec)
        assert key not in blob
        assert bare not in blob.lower()


def test_wallet_balance_with_mocked_rpc(vault, monkeypatch):
    rpc = MockRpc(eth_wei=0, usdc_raw=1_500_000)
    app = build_app(vault, rpc=rpc)
    enroll_principal(app, monkeypatch)
    bal = app.gateway.wallet_balance()
    assert bal["asset"] == "USDC"
    assert bal["chain"] == "base"
    assert bal["amount"] == "1.5"
    assert bal["gas_asset"] == "ETH"
    assert bal["gas_raw"] == "0"
    assert bal["gas_amount"] == "0"
    assert any(c[0] == "eth_call" for c in rpc.calls)


def test_wallet_send_fails_closed_without_eth(app, monkeypatch):
    enroll_principal(app, monkeypatch)
    app.gateway.wallet_address()
    with pytest.raises(NoGas, match="ETH"):
        app.gateway.wallet_send("0x" + "11" * 20, "1")


def test_base_send_without_eth_raises_backend_error_not_contract_type():
    from agentself.internal.eoa import generate_secp256k1

    wallet = WalletAccessFactory(MemoryLog(), rpc=MockRpc(eth_wei=0)).for_binding(
        "base"
    )
    wallet.bind_key(generate_secp256k1())
    with pytest.raises(NoEthForGas, match="EOA has no ETH"):
        wallet.send("P", "0x" + "11" * 20, "1", "USDC")
    assert not hasattr(wallet_contract, "NoEthForGas")


def test_foreign_wallet_refused_before_wallet_access(app, monkeypatch):
    enroll_principal(app, monkeypatch, "P")
    app.keys["Q"] = setup_principal(app.vault, "Q", store="sops")
    app.bind(monkeypatch, "Q")
    app.gateway.enroll("sops")

    before_calls = list(app.wallets.calls)
    before_bind = list(app.wallets.for_binding_calls)
    before_store = list(app.stores.calls)

    with pytest.raises(Refused):
        app.gateway.wallet_address(hold_owner="P")
    with pytest.raises(Refused):
        app.gateway.wallet_sign("x", hold_owner="P")
    with pytest.raises(Refused):
        app.gateway.wallet_balance(hold_owner="P")
    with pytest.raises(Refused):
        app.gateway.wallet_send("0x" + "11" * 20, "1", hold_owner="P")

    assert app.wallets.calls == before_calls
    assert app.wallets.for_binding_calls == before_bind
    assert app.stores.calls == before_store


def test_wallet_access_contract_has_no_key_hex():
    source = _CONTRACT.read_text(encoding="utf-8")
    assert "key_hex" not in source
    assert "NoEthForGas" not in source
    assert not re.search(r"\bETH\b", source)
    assert not re.search(r"\bUSDC\b", source)
    assert not hasattr(wallet_contract, "NoEthForGas")
    members = dict(inspect.getmembers(wallet_contract))
    assert "NoEthForGas" not in members
    assert "ETH" not in members
    assert "USDC" not in members
    for name in ("address", "sign", "balance", "send", "describe"):
        method = getattr(WalletAccess, name)
        params = inspect.signature(method).parameters
        assert "key_hex" not in params
    send_params = list(inspect.signature(WalletAccess.send).parameters)
    assert send_params == ["self", "principal_id", "to", "amount", "asset"]
    assert issubclass(NoEthForGas, WalletCannotSend)


def _is_eoa(address: str) -> bool:
    if not address.startswith("0x") or len(address) != 42:
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in address[2:])


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

    base.bind_key(generate_secp256k1())
    _assert_describe_has_no_key(base.describe("P"))


def test_factory_unknown_wallet_binding_fails():
    with pytest.raises(WalletError, match="unknown wallet binding"):
        WalletAccessFactory(MemoryLog()).for_binding("nope")


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
    enroll_principal(app, monkeypatch)
    bal = app.gateway.wallet_balance()
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
    enroll_principal(app, monkeypatch)
    bal = app.gateway.wallet_balance()
    assert bal["asset"] == "USDC"
    assert bal["amount"] == "0"
    assert bal["gas_asset"] == "ETH"
    assert bal["gas_raw"] == "0"
    assert bal["gas_amount"] == "0"


def test_wallet_send_broadcasts_when_funded(vault, monkeypatch):
    rpc = MockRpc(eth_wei=10**18, usdc_raw=2_000_000)
    app = build_app(vault, rpc=rpc)
    enroll_principal(app, monkeypatch)
    app.gateway.wallet_send("0x" + "11" * 20, "1")
    assert rpc.broadcast
    assert any(c[0] == "eth_sendRawTransaction" for c in rpc.calls)
    key = app.gateway.reveal("wallet.key")
    _assert_key_hidden(app, key)


def test_wallet_send_refuses_without_usdc(vault, monkeypatch):
    from agentself.internal.eoa import generate_secp256k1

    rpc = MockRpc(eth_wei=10**18, usdc_raw=0)
    wallet = WalletAccessFactory(MemoryLog(), rpc=rpc).for_binding("base")
    wallet.bind_key(generate_secp256k1())
    with pytest.raises(WalletCannotSend, match="USDC"):
        wallet.send("P", "0x" + "11" * 20, "1", "USDC")
    assert not rpc.broadcast
    app = build_app(vault, rpc=MockRpc(eth_wei=10**18, usdc_raw=0))
    enroll_principal(app, monkeypatch)
    with pytest.raises(CannotSend, match="USDC"):
        app.gateway.wallet_send("0x" + "11" * 20, "1")
    assert not app.rpc.broadcast


def test_wallet_send_wrong_asset_names_usdc_before_eth_check():
    from agentself.internal.eoa import generate_secp256k1

    wallet = WalletAccessFactory(MemoryLog(), rpc=MockRpc(eth_wei=0)).for_binding(
        "base"
    )
    wallet.bind_key(generate_secp256k1())
    with pytest.raises(WalletCannotSend, match="USDC") as eth_asset:
        wallet.send("P", "0x" + "11" * 20, "1", "ETH")
    assert "EOA has no ETH" not in str(eth_asset.value)
    with pytest.raises(WalletCannotSend, match="USDC") as lower:
        wallet.send("P", "0x" + "11" * 20, "1", "usdc")
    assert "EOA has no ETH" not in str(lower.value)


def test_wallet_send_wrong_asset_gateway_names_usdc(vault, monkeypatch):
    app = build_app(vault, rpc=MockRpc(eth_wei=0))
    enroll_principal(app, monkeypatch)
    with pytest.raises(CannotSend, match="USDC"):
        app.gateway.wallet_send("0x" + "11" * 20, "1", "ETH")


def test_wallet_send_rpc_boom_is_channel_rpc(vault, monkeypatch):
    class BoomRpc:
        def request(self, method: str, params: list[object]) -> object:
            raise WalletError("rpc failed")

    app = build_app(vault, rpc=BoomRpc())
    enroll_principal(app, monkeypatch)
    with pytest.raises(ChannelFailure) as caught:
        app.gateway.wallet_send("0x" + "11" * 20, "1")
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
    enroll_principal(app, monkeypatch)
    with pytest.raises(CannotSend, match="USDC"):
        app.gateway.wallet_send("0x" + "11" * 20, "-1")
    assert not rpc.broadcast

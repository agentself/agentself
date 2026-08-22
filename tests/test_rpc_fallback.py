"""HTTP JSON-RPC fallbacks. Fake opener only — no live sockets."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from agentself.backends.wallet.base import BaseWalletAccess
from agentself.backends.wallet.contract import WalletError
from agentself.backends.wallet.ethereum import EthereumWalletAccess
from agentself.backends.wallet.factory import WalletAccessFactory
from agentself.backends.wallet.rpc import HttpJsonRpc
from agentself.cli.app import main
from agentself.compose import compose as real_compose
from agentself.internal.custody.errors import ChannelFailure
from agentself.internal.eoa import generate_secp256k1
from agentself.internal.log import MemoryLog

from tests.support import (
    FakeRpcOpener,
    MockRpc,
    build_app,
    cli_env,
    enroll_principal,
    run_cli,
)

MAINNET = BaseWalletAccess.default_rpc
PUBLICNODE, DRPC = BaseWalletAccess.fallback_rpcs


def _key_wallet(opener=None, *, rpc=None, rpc_url=None):
    wallet = BaseWalletAccess(MemoryLog(), rpc=rpc, rpc_url=rpc_url, rpc_opener=opener)
    wallet.bind_key(generate_secp256k1())
    return wallet


def test_base_url_order():
    wallet = BaseWalletAccess(MemoryLog())
    assert wallet._rpc_urls() == [MAINNET, PUBLICNODE, DRPC]


def test_http_403_then_publicnode_wins():
    opener = FakeRpcOpener(usdc_raw=1_500_000, eth_wei=10**18)
    opener.fail(MAINNET, 403)
    opener.ok(PUBLICNODE)
    wallet = _key_wallet(opener)
    bal = wallet.balance("P")
    assert bal["asset"] == "USDC"
    assert bal["amount"] == "1.5"
    assert bal["gas_asset"] == "ETH"
    assert bal["gas_raw"] == "1000000000000000000"
    assert MAINNET in opener.urls
    assert PUBLICNODE in opener.urls
    assert opener.urls[0] == MAINNET
    assert DRPC not in opener.urls


def test_gateway_403_then_publicnode(vault, monkeypatch):
    opener = FakeRpcOpener(usdc_raw=2_000_000, eth_wei=0)
    opener.fail(MAINNET, 403)
    opener.ok(PUBLICNODE)
    app = build_app(vault, rpc_opener=opener)
    enroll_principal(app, monkeypatch)
    bal = app.gateway.wallet_balance()
    assert bal["asset"] == "USDC"
    assert bal["amount"] == "2"
    assert bal["gas_asset"] == "ETH"
    assert MAINNET in opener.urls
    assert PUBLICNODE in opener.urls


def test_all_urls_fail_is_rpc_failed():
    opener = FakeRpcOpener()
    opener.fail_all(403)
    wallet = _key_wallet(opener)
    with pytest.raises(WalletError, match="rpc failed"):
        wallet.balance("P")
    assert MAINNET in opener.urls
    assert PUBLICNODE in opener.urls
    assert DRPC in opener.urls


def test_all_urls_fail_gateway_reason_rpc(vault, monkeypatch):
    opener = FakeRpcOpener()
    opener.fail_all(429)
    app = build_app(vault, rpc_opener=opener)
    enroll_principal(app, monkeypatch)
    with pytest.raises(ChannelFailure) as caught:
        app.gateway.wallet_balance()
    assert caught.value.reason == "rpc"


def test_cli_all_urls_fail_json_error_rpc(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    monkeypatch.setenv("AGENTSELF_VAULT_ROOT", env["AGENTSELF_VAULT_ROOT"])
    monkeypatch.setenv("PATH", env["PATH"])
    for key in (
        "AGENTSELF_MAIL_DOMAIN",
        "AGENTSELF_IDENTITY_ID",
        "AGE_KEY_FILE",
    ):
        monkeypatch.delenv(key, raising=False)
    opener = FakeRpcOpener()
    opener.fail_all(403)

    def wrapped(*args, **kwargs):
        kwargs.setdefault("rpc_opener", opener)
        return real_compose(*args, **kwargs)

    monkeypatch.setattr("agentself.cli.app.compose", wrapped, raising=False)
    code = main(["--json", "wallet", "balance"])
    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    err = json.loads(captured.out or captured.err)
    assert err["ok"] is False
    assert err["error"] == "error"
    assert err["reason"] == "rpc"
    assert MAINNET in opener.urls
    assert PUBLICNODE in opener.urls
    assert DRPC in opener.urls


def test_dedup_primary_already_publicnode():
    opener = FakeRpcOpener(usdc_raw=1, eth_wei=1)
    opener.ok(PUBLICNODE)
    opener.fail(DRPC, 403)
    wallet = _key_wallet(opener, rpc_url=PUBLICNODE)
    wallet.balance("P")
    assert opener.urls.count(PUBLICNODE) == 2
    assert MAINNET not in opener.urls
    assert DRPC not in opener.urls


def test_ethereum_empty_rpc_does_not_invent_fallbacks():
    opener = FakeRpcOpener()
    opener.fail_all(403)
    wallet = EthereumWalletAccess(MemoryLog(), rpc_opener=opener)
    wallet.bind_key(generate_secp256k1())
    with pytest.raises(WalletError, match="no RPC configured"):
        wallet.balance("P")
    assert opener.urls == []
    assert wallet._rpc_urls() == []


OVERRIDE = "https://rpc.example.invalid/base"


def test_explicit_rpc_url_skips_base_fallbacks():
    opener = FakeRpcOpener()
    opener.fail(OVERRIDE, 403)
    opener.ok(PUBLICNODE)
    opener.ok(DRPC)
    wallet = _key_wallet(opener, rpc_url=OVERRIDE)
    assert wallet._rpc_urls() == [OVERRIDE]
    with pytest.raises(WalletError, match="rpc failed"):
        wallet.balance("P")
    assert opener.urls == [OVERRIDE]
    assert MAINNET not in opener.urls
    assert PUBLICNODE not in opener.urls
    assert DRPC not in opener.urls


def test_explicit_default_url_still_fails_closed():
    opener = FakeRpcOpener()
    opener.fail(MAINNET, 403)
    opener.ok(PUBLICNODE)
    wallet = _key_wallet(opener, rpc_url=MAINNET)
    assert wallet._rpc_urls() == [MAINNET]
    with pytest.raises(WalletError, match="rpc failed"):
        wallet.balance("P")
    assert opener.urls == [MAINNET]
    assert PUBLICNODE not in opener.urls
    assert DRPC not in opener.urls


def test_factory_base_without_override_keeps_fallbacks():
    wallet = WalletAccessFactory(MemoryLog()).for_binding("base")
    assert wallet._rpc_urls() == [MAINNET, PUBLICNODE, DRPC]


def test_factory_base_override_does_not_fallback():
    opener = FakeRpcOpener()
    opener.fail(OVERRIDE, 403)
    opener.ok(PUBLICNODE)
    wallet = WalletAccessFactory(
        MemoryLog(), eth_rpc_url=OVERRIDE, rpc_opener=opener
    ).for_binding("base")
    wallet.bind_key(generate_secp256k1())
    with pytest.raises(WalletError, match="rpc failed"):
        wallet.balance("P")
    assert opener.urls == [OVERRIDE]
    assert MAINNET not in opener.urls
    assert PUBLICNODE not in opener.urls


def test_gateway_override_reason_rpc(vault, monkeypatch):
    opener = FakeRpcOpener()
    opener.fail(OVERRIDE, 403)
    opener.ok(PUBLICNODE)
    app = build_app(vault, rpc_opener=opener, eth_rpc_url=OVERRIDE)
    enroll_principal(app, monkeypatch)
    with pytest.raises(ChannelFailure) as caught:
        app.gateway.wallet_balance()
    assert caught.value.reason == "rpc"
    assert opener.urls == [OVERRIDE]
    assert MAINNET not in opener.urls
    assert PUBLICNODE not in opener.urls


def test_cli_override_rpc_json_error_rpc_no_fallback(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    monkeypatch.setenv("AGENTSELF_VAULT_ROOT", env["AGENTSELF_VAULT_ROOT"])
    monkeypatch.setenv("PATH", env["PATH"])
    monkeypatch.setenv("AGENTSELF_ETH_RPC_URL", OVERRIDE)
    for key in (
        "AGENTSELF_MAIL_DOMAIN",
        "AGENTSELF_IDENTITY_ID",
        "AGE_KEY_FILE",
    ):
        monkeypatch.delenv(key, raising=False)
    opener = FakeRpcOpener()
    opener.fail(OVERRIDE, 403)
    opener.ok(PUBLICNODE)
    opener.ok(DRPC)

    def wrapped(*args, **kwargs):
        kwargs.setdefault("rpc_opener", opener)
        return real_compose(*args, **kwargs)

    monkeypatch.setattr("agentself.cli.app.compose", wrapped, raising=False)
    code = main(["--json", "wallet", "balance"])
    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    err = json.loads(captured.out or captured.err)
    assert err["ok"] is False
    assert err["error"] == "error"
    assert err["reason"] == "rpc"
    assert "next" in err
    assert opener.urls == [OVERRIDE]
    assert MAINNET not in opener.urls
    assert PUBLICNODE not in opener.urls
    assert DRPC not in opener.urls


def test_injected_mock_rpc_never_calls_opener():
    opener = FakeRpcOpener()
    opener.fail_all(403)
    rpc = MockRpc(eth_wei=10**18, usdc_raw=1_500_000)
    wallet = _key_wallet(opener, rpc=rpc)
    bal = wallet.balance("P")
    assert bal["amount"] == "1.5"
    assert opener.urls == []
    assert any(c[0] == "eth_call" for c in rpc.calls)


def test_factory_unknown_binding_still_no_failover():
    with pytest.raises(WalletError, match="unknown wallet binding"):
        WalletAccessFactory(MemoryLog()).for_binding("cloudflare")
    doc = WalletAccessFactory.__doc__ or ""
    assert "No failover." in doc


def test_http_jsonrpc_retries_json_error_then_succeeds():
    class Opener:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def __call__(self, req, timeout=None):
            self.urls.append(req.full_url)
            if req.full_url == MAINNET:
                return io.BytesIO(
                    b'{"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"busy"}}'
                )
            return io.BytesIO(b'{"jsonrpc":"2.0","id":1,"result":"0x1"}')

    opener = Opener()
    client = HttpJsonRpc(MAINNET, fallbacks=[PUBLICNODE], opener=opener)
    assert client.request("eth_chainId", []) == "0x1"
    assert opener.urls == [MAINNET, PUBLICNODE]


def test_http_jsonrpc_urlerror_then_next():
    class Opener:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def __call__(self, req, timeout=None):
            self.urls.append(req.full_url)
            if req.full_url == MAINNET:
                raise urllib.error.URLError("timed out")
            return io.BytesIO(b'{"jsonrpc":"2.0","id":1,"result":"0x2"}')

    opener = Opener()
    client = HttpJsonRpc(MAINNET, fallbacks=[PUBLICNODE], opener=opener)
    assert client.request("eth_blockNumber", []) == "0x2"
    assert opener.urls == [MAINNET, PUBLICNODE]


def test_http_jsonrpc_empty_is_no_rpc_configured():
    with pytest.raises(WalletError, match="no RPC configured"):
        HttpJsonRpc("").request("eth_chainId", [])


def test_http_jsonrpc_single_url_still_works():
    class Opener:
        def __call__(self, req, timeout=None):
            assert req.full_url == MAINNET
            return io.BytesIO(b'{"jsonrpc":"2.0","id":1,"result":"0x7"}')

    assert HttpJsonRpc(MAINNET, opener=Opener()).request("eth_chainId", []) == "0x7"

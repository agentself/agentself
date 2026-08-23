"""RPC POSTs send a User-Agent. No live sockets."""

from __future__ import annotations

from agentself.backends.wallet.base import BaseWalletAccess
from agentself.backends.wallet.rpc import USER_AGENT, HttpJsonRpc

from tests.support import FakeRpcOpener

MAINNET = BaseWalletAccess.default_rpc


def _ua(headers: dict[str, str]) -> str:
    for key, value in headers.items():
        if key.lower() == "user-agent":
            return value
    return ""


def test_rpc_post_sends_user_agent():
    opener = FakeRpcOpener()
    opener.ok(MAINNET)
    HttpJsonRpc(MAINNET, opener=opener).request("eth_chainId", [])
    assert opener.headers
    ua = _ua(opener.headers[0])
    assert ua == USER_AGENT
    assert "Python-urllib" not in ua
    assert any(k.lower() == "content-type" for k in opener.headers[0])

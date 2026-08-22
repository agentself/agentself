"""RPC POSTs send a User-Agent. 403/429 still try the next URL. No live sockets."""

from __future__ import annotations

import pytest

from agentself.backends.wallet.base import BaseWalletAccess
from agentself.backends.wallet.contract import WalletError
from agentself.backends.wallet.rpc import USER_AGENT, HttpJsonRpc

from tests.support import FakeRpcOpener

MAINNET = BaseWalletAccess.default_rpc
PUBLICNODE, DRPC = BaseWalletAccess.fallback_rpcs


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


@pytest.mark.parametrize("status", [403, 429])
def test_rpc_error_with_ua_tries_next(status):
    opener = FakeRpcOpener()
    opener.fail(MAINNET, status)
    opener.ok(PUBLICNODE)
    result = HttpJsonRpc(MAINNET, fallbacks=[PUBLICNODE], opener=opener).request(
        "eth_chainId", []
    )
    assert result == "0x0"
    assert opener.urls == [MAINNET, PUBLICNODE]
    assert _ua(opener.headers[0]) == USER_AGENT
    assert _ua(opener.headers[1]) == USER_AGENT


def test_all_urls_403_is_rpc_failed():
    opener = FakeRpcOpener()
    opener.fail_all(403)
    with pytest.raises(WalletError, match="rpc failed"):
        HttpJsonRpc(MAINNET, fallbacks=[PUBLICNODE, DRPC], opener=opener).request(
            "eth_chainId", []
        )
    assert opener.urls == [MAINNET, PUBLICNODE, DRPC]
    assert opener.headers
    for hdrs in opener.headers:
        ua = _ua(hdrs)
        assert ua == USER_AGENT
        assert "Python-urllib" not in ua

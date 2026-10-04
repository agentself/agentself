from __future__ import annotations

import io
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agentself.backends.wallet.base import BaseWalletAccess
from agentself.backends.wallet.contract import WalletError
from agentself.backends.wallet.rpc import HttpJsonRpc, NetworkBudget
from agentself.internal.files import identity_home
from agentself.internal.log import MemoryLog

from tests.support import MockRpc

TO = "0x" + "22" * 20
TOKEN = "0x" + "33" * 20


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = 100.0

        def advance(self, seconds):
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(
        "agentself.backends.wallet.rpc.time.monotonic", lambda: clock.now
    )
    return clock


def wallet(tmp_path, *, opener=None, rpc=None):
    return BaseWalletAccess(
        MemoryLog(),
        key_hex="0x" + "11" * 32,
        vault_root=tmp_path,
        rpc_opener=opener,
        rpc=rpc,
    )


def body(result):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()


def test_fallback_consumes_budget_and_direct_requests_start_fresh(clock):
    timeouts = []

    def opener(req, timeout):
        timeouts.append(timeout)
        if req.full_url.endswith("/first"):
            clock.advance(6)
            raise TimeoutError()
        clock.advance(2)
        return io.BytesIO(body("0x1"))

    rpc = HttpJsonRpc(
        "http://local/first", fallbacks=["http://local/second"], opener=opener
    )
    assert rpc.request("eth_chainId", []) == "0x1"
    assert rpc.request("eth_chainId", []) == "0x1"
    assert timeouts == [15, 9, 15, 9]


@pytest.mark.parametrize("response_kind", ["timeout", "bad_body"])
def test_exhausted_attempt_never_starts_fallback(clock, response_kind):
    calls = []

    def opener(req, timeout):
        calls.append(timeout)
        clock.advance(15)
        if response_kind == "timeout":
            raise TimeoutError()
        return io.BytesIO(b"bad json")

    with pytest.raises(WalletError, match="rpc failed"):
        HttpJsonRpc(
            "http://local/first", fallbacks=["http://local/second"], opener=opener
        ).request("eth_chainId", [])
    assert calls == [15]


@pytest.mark.parametrize(
    "operation, expected",
    [
        ("balance", ["eth_getBalance", "eth_call", "eth_call"]),
        (
            "validate_send",
            [
                "eth_getBalance",
                "eth_call",
                "eth_call",
                "eth_gasPrice",
                "eth_estimateGas",
            ],
        ),
        (
            "send",
            [
                "eth_getBalance",
                "eth_call",
                "eth_call",
                "eth_gasPrice",
                "eth_estimateGas",
                "eth_getTransactionCount",
                "eth_sendRawTransaction",
                "eth_getTransactionByHash",
                "eth_getTransactionReceipt",
            ],
        ),
    ],
)
def test_operation_shares_budget_across_all_calls(tmp_path, clock, operation, expected):
    rpc = MockRpc(eth_wei=10**18, usdc_raw=2_000_000)
    attempts = []

    def opener(req, timeout):
        data = json.loads(req.data)
        attempts.append((data["method"], timeout))
        clock.advance(1)
        return io.BytesIO(body(rpc.request(data["method"], data["params"])))

    access = wallet(tmp_path, opener=opener)
    if operation == "balance":
        assert access.balance("P", TOKEN)["amount"] == "2"
    else:
        assert getattr(access, operation)("P", TO, "1", TOKEN) == TOKEN
    assert attempts == list(zip(expected, range(15, 15 - len(expected), -1)))


@pytest.mark.parametrize("injected", [False, True])
def test_balance_expiry_and_next_operation_fresh_budget(tmp_path, clock, injected):
    timeouts = []

    def opener(req, timeout):
        timeouts.append(timeout)
        clock.advance(15)
        return io.BytesIO(body("0x1"))

    rpc = HttpJsonRpc("http://local/", opener=opener) if injected else None
    access = wallet(tmp_path, rpc=rpc, opener=opener)
    with pytest.raises(WalletError):
        access.balance("P")
    assert access.balance("P", "ETH")["raw"] == "1"
    assert timeouts == [15, 15]


def test_substitute_rpc_keeps_two_argument_contract_and_stops_at_expiry(
    tmp_path, clock
):
    class Rpc(MockRpc):
        def request(self, method, params):
            clock.advance(15)
            return super().request(method, params)

    rpc = Rpc(eth_wei=10**18)
    with pytest.raises(WalletError):
        wallet(tmp_path, rpc=rpc).validate_send("P", TO, "1", "USDC")
    assert [method for method, _ in rpc.calls] == ["eth_getBalance"]


@pytest.mark.parametrize(
    "expire_at", ["eth_gasPrice", "eth_getTransactionCount", "eth_sendRawTransaction"]
)
def test_send_expiry_preserves_pending_and_same_raw_retry(tmp_path, clock, expire_at):
    rpc = MockRpc(eth_wei=10**18, usdc_raw=2_000_000)
    attempts = []
    expire = True

    def opener(req, timeout):
        data = json.loads(req.data)
        method, params = data["method"], data["params"]
        attempts.append((method, params))
        if expire and method == expire_at:
            clock.advance(15)
            if method == "eth_sendRawTransaction":
                raise TimeoutError()
        return io.BytesIO(body(rpc.request(method, params)))

    access = wallet(tmp_path, opener=opener)
    with pytest.raises(WalletError, match="rpc failed"):
        access.send("P", TO, "1", "USDC")
    assert attempts[-1][0] == expire_at
    pending = identity_home(tmp_path, "P") / "wallet" / "pending-send.json"
    if expire_at == "eth_gasPrice":
        assert not pending.exists()
        assert not rpc.sent_raw
        return
    record = json.loads(pending.read_text())
    if expire_at == "eth_sendRawTransaction":
        assert attempts[-1][1] == [record["raw"]]
    else:
        assert not any(method == "eth_sendRawTransaction" for method, _ in attempts)
    assert access.payment_ref() == ""
    expire = False
    access.send("P", TO, "1", "USDC")
    assert rpc.sent_raw == [record["raw"]]
    assert sum(method == "eth_getTransactionCount" for method, _ in attempts) == 1


@pytest.mark.parametrize("lost_response", [False, True])
def test_accepted_broadcast_at_expiry_keeps_pending_until_next_confirmation(
    tmp_path, clock, lost_response
):
    rpc = MockRpc(eth_wei=10**18, usdc_raw=2_000_000)
    attempts = []

    def opener(req, timeout):
        data = json.loads(req.data)
        method = data["method"]
        attempts.append(method)
        result = rpc.request(method, data["params"])
        if method == "eth_sendRawTransaction":
            clock.advance(15)
            if lost_response:
                raise TimeoutError()
        return io.BytesIO(body(result))

    access = wallet(tmp_path, opener=opener)
    if lost_response:
        with pytest.raises(WalletError):
            access.send("P", TO, "1", "USDC")
    else:
        assert access.send("P", TO, "1", "USDC") == "USDC"
        assert access.payment_ref() == rpc.tx_hash
    pending = identity_home(tmp_path, "P") / "wallet" / "pending-send.json"
    assert pending.is_file()
    assert attempts[-1] == "eth_sendRawTransaction"
    access.send("P", TO, "1", "USDC")
    assert len(rpc.sent_raw) == 1
    assert not pending.exists()
    assert access.payment_ref() == rpc.tx_hash


def test_body_read_consumes_budget_and_response_is_closed(tmp_path, clock):
    responses = []

    class Response(io.BytesIO):
        def read(self, size=-1):
            clock.advance(16)
            return super().read(size)

    def opener(req, timeout):
        response = Response(body("0x1"))
        responses.append(response)
        return response

    with pytest.raises(WalletError):
        wallet(tmp_path, opener=opener).balance("P")
    assert len(responses) == 1
    assert responses[0].closed


def test_expired_budget_starts_no_transport(clock):
    budget = NetworkBudget()
    clock.advance(15)

    def opener(*args, **kwargs):
        pytest.fail("transport started after expiry")

    with pytest.raises(WalletError):
        HttpJsonRpc("http://local/", opener=opener).request(
            "eth_chainId", [], budget=budget
        )


def test_local_http_fallback_uses_remaining_timeout(clock):
    paths = []
    timeouts = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            paths.append(self.path)
            clock.advance(6)
            data = b"invalid" if self.path == "/first" else body("0x1")
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def opener(req, timeout):
        timeouts.append(timeout)
        return direct.open(req, timeout=timeout)

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 2
        thread = threading.Thread(
            target=lambda: [server.handle_request() for _ in range(2)], daemon=True
        )
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            rpc = HttpJsonRpc(
                url + "/first", fallbacks=[url + "/second"], opener=opener
            )
            assert rpc.request("eth_chainId", []) == "0x1"
        finally:
            thread.join(timeout=5)
        assert not thread.is_alive()
    assert paths == ["/first", "/second"]
    assert timeouts == [15, 9]

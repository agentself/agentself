"""Live chain send-through-a-contract against anvil, skipped when anvil is absent."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from eth_utils import keccak, to_checksum_address

from tests.support import cli_env, run_cli, value_file

pytestmark = pytest.mark.skipif(
    shutil.which("anvil") is None, reason="anvil is not on PATH"
)

_SOLC = shutil.which("solc")
_FORGE = shutil.which("forge")

_TOKEN_SRC = """\
pragma solidity ^0.8.24;
contract Token {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    uint8 public decimals = 6;
    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }
    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }
    function transfer(address to, uint256 amount) external returns (bool) {
        require(balanceOf[msg.sender] >= amount, "bal");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }
    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(balanceOf[from] >= amount, "bal");
        require(allowance[from][msg.sender] >= amount, "allow");
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}
"""

_GATEWAY_SRC = """\
pragma solidity ^0.8.24;
interface IERC20 {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}
contract Gateway {
    IERC20 public token;
    mapping(uint256 => bool) public paid;
    constructor(address token_) { token = IERC20(token_); }
    function pay(uint256 orderId, uint256 amount) external {
        require(!paid[orderId], "paid");
        paid[orderId] = true;
        require(token.transferFrom(msg.sender, address(this), amount), "pull");
    }
}
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _rpc(url: str, method: str, params: list[object]) -> object:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("error"):
        raise RuntimeError(body["error"])
    return body["result"]


def _wait_rpc(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _rpc(url, "eth_chainId", [])
            return
        except (TimeoutError, urllib.error.URLError, OSError):
            time.sleep(0.1)
    raise RuntimeError("anvil did not become ready")


def _compile(tmp_path: Path, name: str, source: str) -> str:
    src = tmp_path / f"{name}.sol"
    src.write_text(source, encoding="utf-8")
    if _SOLC:
        proc = subprocess.run(
            [_SOLC, "--bin", "--optimize", str(src)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        binary = lines[-1]
        assert all(ch in "0123456789abcdefABCDEF" for ch in binary)
        return "0x" + binary
    if _FORGE:
        out = tmp_path / "out"
        proc = subprocess.run(
            [_FORGE, "build", "--contracts", str(src), "--out", str(out)],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        matches = list(out.rglob(f"{name}.json"))
        assert matches, list(out.rglob("*"))
        artifact = json.loads(matches[0].read_text(encoding="utf-8"))
        bytecode = artifact.get("bytecode", {})
        if isinstance(bytecode, dict):
            hexed = str(bytecode.get("object") or "")
        else:
            hexed = str(bytecode)
        assert hexed.startswith("0x")
        return hexed
    pytest.skip("solc or forge is required to compile anvil fixtures")


def _deploy(url: str, deployer: str, bytecode: str) -> str:
    tx_hash = str(
        _rpc(
            url,
            "eth_sendTransaction",
            [{"from": deployer, "data": bytecode, "gas": hex(2_000_000)}],
        )
    )
    receipt = None
    for _ in range(20):
        receipt = _rpc(url, "eth_getTransactionReceipt", [tx_hash])
        if receipt:
            break
        time.sleep(0.1)
    assert isinstance(receipt, dict), receipt
    address = str(receipt.get("contractAddress") or "")
    assert address.startswith("0x")
    return to_checksum_address(address)


def _encode_address(addr: str) -> str:
    return addr.lower().removeprefix("0x").rjust(64, "0")


def _calldata(signature: str, args_hex: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex() + args_hex


@pytest.fixture
def anvil(tmp_path: Path):
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [
            "anvil",
            "--chain-id",
            "8453",
            "--port",
            str(port),
            "--silent",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        cwd=str(tmp_path),
    )
    try:
        _wait_rpc(url)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_anvil_allow_then_pay_and_named_balance(anvil: str, tmp_path: Path):
    deployer = to_checksum_address(str(_rpc(anvil, "eth_accounts", [])[0]))
    token = _deploy(anvil, deployer, _compile(tmp_path, "Token", _TOKEN_SRC))
    gateway_code = _compile(tmp_path, "Gateway", _GATEWAY_SRC)
    gateway = _deploy(anvil, deployer, gateway_code + _encode_address(token))

    env = cli_env(tmp_path / "vault")
    env["AGENTSELF_ETH_RPC_URL"] = anvil
    started = run_cli(["init", "--wallet", "base"], env)
    assert started.returncode == 0, started.stderr
    identity = json.loads(started.stdout)
    owner = identity["address"]

    mint = _calldata(
        "mint(address,uint256)",
        _encode_address(owner) + format(2_000_000, "x").zfill(64),
    )
    fund = _rpc(
        anvil,
        "eth_sendTransaction",
        [
            {
                "from": deployer,
                "to": token,
                "data": mint,
                "gas": hex(200_000),
            }
        ],
    )
    assert str(fund).startswith("0x")
    _rpc(
        anvil,
        "anvil_setBalance",
        [owner, hex(10**18)],
    )

    named = run_cli(["wallet", "balance", token], env)
    assert named.returncode == 0, named.stderr
    bal = json.loads(named.stdout)
    assert bal["asset"].lower() == token.lower()
    assert bal["amount"] == "2"

    allow = value_file(tmp_path, '{"allow": true}\n', "allow.json")
    approved = run_cli(
        ["wallet", "send", gateway, "1", token, "--file", allow],
        env,
    )
    assert approved.returncode == 0, approved.stdout + approved.stderr
    pay = value_file(
        tmp_path,
        json.dumps({"signature": "pay(uint256,uint256)", "args": ["1", "1000000"]})
        + "\n",
        "pay.json",
    )
    paid = run_cli(["wallet", "send", gateway, "1", token, "--file", pay], env)
    assert paid.returncode == 0, paid.stdout + paid.stderr
    data = json.loads(paid.stdout)
    assert data["ok"] is True
    assert data["hash"].startswith("0x")
    assert "ETH" not in data["asset"]
    leftover = json.loads(run_cli(["wallet", "balance", token], env).stdout)
    assert leftover["amount"] == "1"
    paid_flag = _rpc(
        anvil,
        "eth_call",
        [
            {
                "to": gateway,
                "data": _calldata("paid(uint256)", format(1, "x").zfill(64)),
            },
            "latest",
        ],
    )
    assert int(str(paid_flag), 16) == 1

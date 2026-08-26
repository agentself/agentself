"""Typed statements use typed data; other files stay personal signatures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data

from agentself.backends.wallet.chain import (
    _encode_statement,
    _statement_scheme,
    _typed_statement,
)
from agentself.backends.wallet.contract import CannotAuthorize
from agentself.internal.custody.errors import CannotAuthorize as CustodyCannotAuthorize

from tests.support import cli_env, run_cli

SIWE = (
    "example.com wants you to sign in with your Ethereum account:\n"
    "0x6cBC7E018A7773dc16a7e4b57E8D524332f4a880\n\n"
    "URI: https://example.com\n"
    "Version: 1\n"
    "Chain ID: 8453\n"
    "Nonce: 12345678\n"
    "Issued At: 2026-08-25T00:00:00.000Z"
)

TYPED = {
    "types": {
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ],
        "ForwardRequest": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "gas", "type": "uint256"},
            {"name": "nonce", "type": "uint256"},
            {"name": "data", "type": "bytes"},
        ],
    },
    "primaryType": "ForwardRequest",
    "domain": {
        "name": "MinimalForwarder",
        "version": "0.0.1",
        "chainId": 8453,
        "verifyingContract": "0x0000000000000000000000000000000000000001",
    },
    "message": {
        "from": "0x6cBC7E018A7773dc16a7e4b57E8D524332f4a880",
        "to": "0x0000000000000000000000000000000000000002",
        "value": 0,
        "gas": 100000,
        "nonce": 0,
        "data": "0x",
    },
}


def _init(tmp_path: Path) -> dict[str, str]:
    env = cli_env(tmp_path / "vault")
    started = run_cli(["--json", "init"], env)
    assert started.returncode == 0, started.stderr
    return env


def test_plain_json_and_login_text_stay_personal() -> None:
    assert _typed_statement('{"hello": "world"}') is None
    assert _statement_scheme('{"hello": "world"}') == "eip191"
    assert _typed_statement(SIWE) is None
    assert _statement_scheme(SIWE) == "eip191"
    encoded = _encode_statement(SIWE)
    assert encoded == encode_defunct(text=SIWE)


def test_typed_statement_is_typed_data() -> None:
    blob = json.dumps(TYPED)
    assert _typed_statement(blob) is not None
    assert _statement_scheme(blob) == "eip712"
    assert _encode_statement(blob) == encode_typed_data(full_message=TYPED)


def test_malformed_typed_statement_refuses_instead_of_personal() -> None:
    blob = json.dumps({"domain": {}, "types": {"Mail": "nope"}, "message": {}})
    assert _typed_statement(blob) is not None
    with pytest.raises(CannotAuthorize):
        _encode_statement(blob)


def test_cli_typed_authorize_verifies_and_names_scheme(tmp_path: Path) -> None:
    env = _init(tmp_path)
    path = tmp_path / "typed.json"
    path.write_text(json.dumps(TYPED), encoding="utf-8")
    auth = json.loads(
        run_cli(["--json", "wallet", "authorize", "--file", str(path)], env).stdout
    )
    assert auth["ok"] is True
    assert auth["scheme"] == "eip712"
    recovered = Account.recover_message(
        encode_typed_data(full_message=TYPED),
        signature=auth["authorization"],
    )
    assert recovered == auth["address"]
    personal = Account.recover_message(
        encode_defunct(text=json.dumps(TYPED)),
        signature=auth["authorization"],
    )
    assert personal != auth["address"]
    checked = json.loads(
        run_cli(
            ["--json", "wallet", "verify", "--file", str(path), auth["authorization"]],
            env,
        ).stdout
    )
    assert checked["ok"] is True
    assert checked["valid"] is True
    assert checked["scheme"] == "eip712"
    assert checked["address"] == auth["address"]


def test_cli_login_text_stays_personal(tmp_path: Path) -> None:
    env = _init(tmp_path)
    path = tmp_path / "login.txt"
    path.write_text(SIWE, encoding="utf-8")
    auth = json.loads(
        run_cli(["--json", "wallet", "authorize", "--file", str(path)], env).stdout
    )
    assert auth["scheme"] == "eip191"
    recovered = Account.recover_message(
        encode_defunct(text=SIWE),
        signature=auth["authorization"],
    )
    assert recovered == auth["address"]
    checked = json.loads(
        run_cli(
            ["--json", "wallet", "verify", "--file", str(path), auth["authorization"]],
            env,
        ).stdout
    )
    assert checked["scheme"] == "eip191"
    assert checked["valid"] is True


def test_cli_malformed_typed_statement_is_refused(tmp_path: Path) -> None:
    env = _init(tmp_path)
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps({"domain": {}, "types": {"Mail": "nope"}, "message": {}}),
        encoding="utf-8",
    )
    failed = run_cli(["--json", "wallet", "authorize", "--file", str(path)], env)
    assert failed.returncode == 2
    data = json.loads(failed.stdout)
    assert data["error"] == "refused"
    assert data["reason"] == "backend cannot authorize"


def test_client_malformed_typed_statement_is_cannot_authorize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support import apply_cli_env, build_app, init_identity

    vault = tmp_path / "vault"
    env = cli_env(vault)
    apply_cli_env(monkeypatch, env)
    app = build_app(vault)
    init_identity(app, monkeypatch)
    blob = json.dumps({"domain": {}, "types": {"Mail": "nope"}, "message": {}})
    with pytest.raises(CustodyCannotAuthorize):
        app.client.wallet_authorize(blob)

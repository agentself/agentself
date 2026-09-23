"""Authorization prepares a wallet once, and one operation reads config once."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from agentself.cli.app import main
from agentself.compose import compose
from agentself.internal.eoa import generate_secp256k1

from tests.support import (
    apply_cli_env,
    build_app,
    cli_env,
    init_identity,
    run_cli,
)

MESSAGE = "prove custody"


def _counts(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    import agentself.internal.files as files
    import agentself.local as local

    counts = {"config": 0, "age": 0, "decrypt": 0}
    real_run = files.subprocess.run
    real_load = local.load_json_file

    def run(argv, *args, **kwargs):
        cmd = [str(part) for part in argv]
        base = Path(cmd[0]).name.lower() if cmd else ""
        if "age-keygen" in base:
            counts["age"] += 1
        if "sops" in base and "--decrypt" in cmd:
            counts["decrypt"] += 1
        return real_run(argv, *args, **kwargs)

    def load(path):
        if Path(path).name == "config.json":
            counts["config"] += 1
        return real_load(path)

    monkeypatch.setattr(files.subprocess, "run", run)
    monkeypatch.setattr(local, "load_json_file", load)
    return counts


def _init(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr
    return vault, env


def test_json_authorize_decrypts_and_binds_once(tmp_path, monkeypatch, capsys):
    vault, env = _init(tmp_path)
    apply_cli_env(monkeypatch, env)
    statement = tmp_path / "statement.txt"
    statement.write_bytes(MESSAGE.encode("utf-8"))
    counts = _counts(monkeypatch)

    def mailbox(*_args, **_kwargs):
        raise AssertionError("wallet authorize must not open email")

    monkeypatch.setattr(
        "agentself.internal.custody.manager.CustodyManager._mailbox_for",
        mailbox,
    )
    assert main(["--json", "wallet", "authorize", "--file", str(statement)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["scheme"] == "eip191"
    assert data["network"] == "base"
    assert data["message_sha256"]
    recovered = Account.recover_message(
        encode_defunct(text=MESSAGE), signature=data["authorization"]
    )
    assert recovered == data["address"]
    assert counts == {"config": 1, "age": 1, "decrypt": 1}


def test_raw_authorize_still_decrypts_once(tmp_path, monkeypatch, capsys):
    _vault, env = _init(tmp_path)
    apply_cli_env(monkeypatch, env)
    counts = _counts(monkeypatch)
    assert main(["--raw", "wallet", "authorize", MESSAGE]) == 0
    token = capsys.readouterr().out.strip()
    assert token.startswith("0x")
    assert counts["decrypt"] == 1
    assert counts["age"] == 1
    assert counts["config"] == 1


def test_malformed_typed_authorize_stops_after_one_decrypt(
    tmp_path, monkeypatch, capsys
):
    _vault, env = _init(tmp_path)
    apply_cli_env(monkeypatch, env)
    blob = json.dumps({"domain": {}, "types": {"Mail": "nope"}, "message": {}})
    path = tmp_path / "bad.json"
    path.write_text(blob, encoding="utf-8")
    counts = _counts(monkeypatch)
    assert main(["--json", "wallet", "authorize", "--file", str(path)]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["reason"] == "typed encoding required"
    assert counts["decrypt"] == 1
    assert counts["age"] == 1


def test_ethereum_authorize_uses_that_backend_once(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    env["AGENTSELF_ETH_RPC_URL"] = "http://127.0.0.1:9"
    started = run_cli(["init", "--wallet", "ethereum"], env)
    assert started.returncode == 0, started.stderr
    apply_cli_env(monkeypatch, env)
    monkeypatch.setenv("AGENTSELF_ETH_RPC_URL", "http://127.0.0.1:9")
    counts = _counts(monkeypatch)
    assert main(["--json", "wallet", "authorize", MESSAGE]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["network"] == "ethereum"
    assert data["scheme"] == "eip191"
    assert counts["decrypt"] == 1
    assert counts["age"] == 1


def test_synthetic_authorize_binds_material_once(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    app.client.wallet_address()
    app.stores.calls.clear()
    app.wallets.instances.clear()
    app.wallets.for_binding_calls.clear()
    app.mailboxes.for_binding_calls.clear()
    monkeypatch.setattr(
        "agentself.cli.commands.wallet.client", lambda _vault: app.client
    )
    monkeypatch.setattr(
        "agentself.internal.host_tools.ensure_host_tools", lambda fetch=False: None
    )
    assert main(["--json", "wallet", "authorize", MESSAGE]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["address"] == "note.1"
    assert data["scheme"] == "ed25519"
    inst = app.wallets.instances[-1]
    binds = [call for call in inst.calls if call[0] == "bind_material"]
    assert binds == [("bind_material", "synthetic-note-seed")]
    assert [call for call in inst.calls if call[0] == "authorize"] == [("authorize",)]
    assert ("verify",) in inst.calls
    assert ("describe",) in inst.calls
    gets = [call for call in app.stores.calls if call[0] == "get"]
    assert len(gets) == 1
    assert app.mailboxes.for_binding_calls == []


def test_reused_client_sees_wallet_key_update(tmp_path, monkeypatch):
    vault, env = _init(tmp_path)
    apply_cli_env(monkeypatch, env)
    client = compose(vault)
    sig1 = client.wallet_authorize("alpha")
    addr1 = client.wallet_address()
    client.update("wallet.key", generate_secp256k1(), unsafe=True)
    sig2 = client.wallet_authorize("alpha")
    addr2 = client.wallet_address()
    assert sig1 != sig2
    assert addr1 != addr2
    recovered = Account.recover_message(encode_defunct(text="alpha"), signature=sig2)
    assert recovered == addr2


def test_later_operation_rereads_config_and_new_client_sees_env(tmp_path, monkeypatch):
    vault, env = _init(tmp_path)
    apply_cli_env(monkeypatch, env)
    client = compose(vault)
    assert client.identity()["wallet"]["chain"] == "base"
    import agentself.local as local

    reads: list[Path] = []
    real_load = local.load_json_file

    def load(path):
        if Path(path).name == "config.json":
            reads.append(Path(path))
        return real_load(path)

    monkeypatch.setattr(local, "load_json_file", load)
    client.wallet_address()
    assert len(reads) == 1
    monkeypatch.setenv("AGENTSELF_WALLET_BACKEND", "ethereum")
    monkeypatch.setenv("AGENTSELF_ETH_RPC_URL", "http://127.0.0.1:9")
    assert client.identity()["wallet"]["chain"] == "base"
    fresh = compose(vault)
    assert fresh.identity()["wallet"]["chain"] == "ethereum"


def test_secret_list_and_address_read_config_once(tmp_path, monkeypatch, capsys):
    _vault, env = _init(tmp_path)
    apply_cli_env(monkeypatch, env)
    counts = _counts(monkeypatch)
    assert main(["secret", "list"]) == 0
    capsys.readouterr()
    assert counts == {"config": 1, "age": 1, "decrypt": 0}
    counts["config"] = counts["age"] = counts["decrypt"] = 0
    assert main(["wallet", "address"]) == 0
    assert counts == {"config": 1, "age": 1, "decrypt": 1}

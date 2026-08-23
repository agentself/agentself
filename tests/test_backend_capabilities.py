"""Backend capability metadata and local-only semantics."""

from __future__ import annotations

import json

from agentself.backends.store.factory import StoreAccessFactory
from agentself.host import CHANNELS
from agentself.internal.log import MemoryLog

from tests.support import cli_env, run_cli, value_file

TOKEN_CANARY = "hold-token-CANARY-doctor-mailbox"
ADDRESS_CANARY = "imap-address-CANARY@example.com"


def test_store_catalog_tools_match_runtime_requirements(tmp_path):
    factory = StoreAccessFactory(tmp_path, MemoryLog())
    for binding in CHANNELS["store"].names:
        bind = next(item for item in CHANNELS["store"].binds if item.name == binding)
        runtime = factory.for_binding(binding).required_tools()
        assert tuple(tool.name for tool in runtime) == bind.tools
        assert (
            tuple(tool.name for tool in runtime if tool.installable)
            == bind.installable_tools
        )


def test_diagnose_agentmail_token_canary_is_absent(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    started = run_cli(["init", "--email", "agentmail"], env)
    assert started.returncode == 0, started.stderr
    missing = run_cli(["--json", "diagnose"], env)
    assert missing.returncode == 0, missing.stdout + missing.stderr
    data = json.loads(missing.stdout)
    assert data["ok"] is True
    assert data["ready"]["email"] is False
    blob = missing.stdout + missing.stderr
    assert TOKEN_CANARY not in blob
    sealed = run_cli(
        [
            "secret",
            "create",
            "email.credential",
            "--file",
            value_file(tmp_path, TOKEN_CANARY),
        ],
        env,
    )
    assert sealed.returncode == 0, sealed.stderr
    after = run_cli(["--json", "diagnose"], env)
    assert after.returncode == 0, after.stdout + after.stderr
    again = json.loads(after.stdout)
    assert again["ok"] is True
    assert again["ready"]["email"] is False
    dumped = json.dumps(again)
    assert TOKEN_CANARY not in after.stdout + after.stderr + dumped


def test_diagnose_imap_address_ready_after_secrets(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    started = run_cli(["init", "--email", "imap"], env)
    assert started.returncode == 0, started.stderr
    token = run_cli(
        [
            "secret",
            "create",
            "email.credential",
            "--file",
            value_file(tmp_path, TOKEN_CANARY),
        ],
        env,
    )
    assert token.returncode == 0, token.stderr
    missing = run_cli(["--json", "diagnose"], env)
    assert missing.returncode == 0, missing.stdout + missing.stderr
    data = json.loads(missing.stdout)
    assert data["ok"] is True
    assert data["ready"]["email"] is False
    blob = missing.stdout + missing.stderr + json.dumps(data)
    assert TOKEN_CANARY not in blob
    addr = run_cli(
        [
            "secret",
            "create",
            "email.address",
            "--file",
            value_file(tmp_path, ADDRESS_CANARY, "addr.txt"),
        ],
        env,
    )
    assert addr.returncode == 0, addr.stderr
    ok = run_cli(["--json", "diagnose"], env)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    ready = json.loads(ok.stdout)
    assert ready["ok"] is True
    assert ready["ready"]["email"] is True
    dumped = ok.stdout + ok.stderr + json.dumps(ready)
    assert TOKEN_CANARY not in dumped
    assert ADDRESS_CANARY not in dumped

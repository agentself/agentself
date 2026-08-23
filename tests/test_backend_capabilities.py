"""Backend capability metadata and local-only semantics."""

from __future__ import annotations

import inspect
import json
import re

from agentself.backends.email.factory import MailboxAccessFactory
from agentself.backends.store.factory import StoreAccessFactory
from agentself.backends.wallet.factory import WalletAccessFactory
from agentself.host import (
    CHANNELS,
    default_wallet_asset,
)

from tests.support import cli_env, run_cli, value_file

TOKEN_CANARY = "hold-token-CANARY-doctor-mailbox"
ADDRESS_CANARY = "imap-address-CANARY@example.com"


def _catalog_bind(channel: str, name: str):
    for item in CHANNELS[channel].binds:
        if item.name == name:
            return item
    raise AssertionError(f"{channel} backend {name} missing")


def test_catalog_names_match_factories():
    def equals_binds(factory) -> set[str]:
        return set(
            re.findall(r'binding == "([^"]+)"', inspect.getsource(factory.for_binding))
        )

    wallet = equals_binds(WalletAccessFactory)
    mailbox = equals_binds(MailboxAccessFactory)
    store = equals_binds(StoreAccessFactory)
    assert set(CHANNELS["wallet"].names) <= wallet
    assert set(CHANNELS["email"].names) <= mailbox
    assert set(CHANNELS["store"].names) == store
    assert tuple(CHANNELS) == ("wallet", "email", "store")
    assert "sms" not in CHANNELS
    assert "local-account" not in CHANNELS["wallet"].names
    assert "maildir" not in CHANNELS["email"].names
    assert "routing" not in CHANNELS["email"].names


def test_json_backends_wallet_is_live(tmp_path):
    env = cli_env(tmp_path / "vault")
    data = json.loads(run_cli(["--json", "backends", "wallet"], env).stdout)
    binds = {item["name"]: item for item in data["channel"]["backends"]}
    assert "local-account" not in binds
    assert binds["base"]["live"] is True
    assert binds["ethereum"]["live"] is True
    assert binds["base"]["custody"] == "eoa-key"


def test_sms_is_not_a_public_channel(tmp_path):
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["--json", "backends", "sms"], env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    data = json.loads(proc.stdout or proc.stderr)
    assert data["ok"] is False
    assert "sms" in data["reason"] or "invalid choice" in data["reason"]


def test_other_local_and_live_binds():
    assert _catalog_bind("email", "agentmail").live is True
    assert _catalog_bind("email", "imap").live is True
    assert _catalog_bind("store", "sops").live is False
    assert _catalog_bind("store", "sops").custody == "age-files"
    assert _catalog_bind("store", "pass").live is False
    assert _catalog_bind("store", "pass").custody == "gpg-pass"


def test_omitted_asset_follows_catalog():
    assert default_wallet_asset("base", "") == "USDC"
    assert default_wallet_asset("ethereum", "") == "USDC"
    assert default_wallet_asset("base", "USDC") == "USDC"


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

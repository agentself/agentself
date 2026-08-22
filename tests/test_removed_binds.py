"""Removed binds fail closed. No migration onto live vendors."""

from __future__ import annotations

import json

from agentself.backends.email.contract import MailboxError
from agentself.backends.email.factory import MailboxAccessFactory
from agentself.backends.wallet.contract import WalletError
from agentself.backends.wallet.factory import WalletAccessFactory
from agentself.internal.log import MemoryLog

from tests.support import cli_env, run_cli


def test_unknown_wallet_binds_fail_closed(tmp_path):
    env = cli_env(tmp_path / "vault")
    for value in ("local-account", "account"):
        proc = run_cli(["init", "--wallet", value], env)
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert f"unknown wallet backend: {value}" in proc.stderr
        assert "next: agentself backends wallet" in proc.stderr


def test_unknown_mailbox_binds_fail_closed(tmp_path):
    env = cli_env(tmp_path / "vault")
    for value in ("maildir", "routing"):
        proc = run_cli(["init", "--email", value], env)
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert f"unknown email backend: {value}" in proc.stderr
        assert "next: agentself backends email" in proc.stderr


def test_sms_flag_and_command_are_unknown(tmp_path):
    env = cli_env(tmp_path / "vault")
    flagged = run_cli(["init", "--sms", "file"], env)
    assert flagged.returncode == 2, flagged.stdout + flagged.stderr
    assert "unrecognized arguments: --sms" in flagged.stderr
    cmd = run_cli(["sms"], env)
    assert cmd.returncode == 2, cmd.stdout + cmd.stderr
    assert "invalid choice: 'sms'" in cmd.stderr
    send = run_cli(["sms", "send", "+15555550100", "hi"], env)
    assert send.returncode == 2, send.stdout + send.stderr
    backends = run_cli(["backends", "sms"], env)
    assert backends.returncode == 2, backends.stdout + backends.stderr
    assert "invalid choice: 'sms'" in backends.stderr


def test_recorded_vault_removed_binds_fail_closed(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr
    cfg_path = vault / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    for key, value in (
        ("wallet_backend", "local-account"),
        ("wallet_backend", "account"),
        ("email_backend", "maildir"),
        ("email_backend", "routing"),
    ):
        written = dict(cfg)
        written[key] = value
        cfg_path.write_text(json.dumps(written, indent=2) + "\n", encoding="utf-8")
        shown = run_cli(["show"], env)
        assert shown.returncode == 2, shown.stdout + shown.stderr
        channel = "wallet" if key.startswith("wallet") else "email"
        assert f"unknown {channel} backend: {value}" in shown.stderr


def test_env_removed_binds_fail_closed(tmp_path):
    env = cli_env(tmp_path / "vault")
    env["AGENTSELF_WALLET_BACKEND"] = "local-account"
    proc = run_cli(["init"], env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "unknown wallet backend: local-account" in proc.stderr
    env.pop("AGENTSELF_WALLET_BACKEND")
    env["AGENTSELF_EMAIL_BACKEND"] = "maildir"
    proc = run_cli(["init"], env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "unknown email backend: maildir" in proc.stderr


def test_product_factories_reject_removed_binds(vault):
    log = MemoryLog()
    for name in ("local-account", "account"):
        try:
            WalletAccessFactory(log).for_binding(name)
        except WalletError as exc:
            assert "unknown wallet binding" in str(exc)
        else:
            raise AssertionError(name)
    for name in ("maildir", "routing"):
        try:
            MailboxAccessFactory(vault, log).for_binding(name)
        except MailboxError as exc:
            assert "unknown mailbox binding" in str(exc)
        else:
            raise AssertionError(name)

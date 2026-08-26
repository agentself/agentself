"""Public CLI help: featured commands and flags exist; generic help stays provider-neutral."""

from __future__ import annotations

import json
import re

from agentself.host import CHANNELS

from tests.support import cli_env, run_cli

_FEATURED_TOP = (
    "init",
    "show",
    "backends",
    "commands",
    "diagnose",
    "secret",
    "email",
    "wallet",
    "backup",
    "restore",
    "install",
)

_OPERATION_HELPS = (
    ("secret", "--help"),
    ("secret", "create", "--help"),
    ("secret", "get", "--help"),
    ("email", "--help"),
    ("email", "connect", "--help"),
    ("email", "show", "--help"),
    ("email", "send", "--help"),
    ("email", "receive", "--help"),
    ("email", "list", "--help"),
    ("wallet", "--help"),
    ("wallet", "show", "--help"),
    ("wallet", "address", "--help"),
    ("wallet", "balance", "--help"),
    ("wallet", "authorize", "--help"),
    ("wallet", "verify", "--help"),
    ("wallet", "send", "--help"),
)


def _has_token(text: str, token: str) -> bool:
    return (
        re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", text.lower()) is not None
    )


def test_top_help_lists_commands_and_flags(tmp_path):
    proc = run_cli(["--help"], cli_env(tmp_path / "vault"))
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout
    for verb in _FEATURED_TOP:
        assert verb in text, verb
    assert "--json" not in text
    assert "--raw" in text
    assert "--version" in text
    assert "AGENTSELF_IDENTITY_DIR" in text
    assert "0x" not in text


def test_nested_help_exposes_arguments_not_providers(tmp_path):
    env = cli_env(tmp_path / "vault")
    create = run_cli(["secret", "create", "--help"], env)
    assert create.returncode == 0, create.stderr
    assert "--json" not in create.stdout
    assert "NAME" in create.stdout
    assert "VALUE" in create.stdout
    assert "--file" in create.stdout
    assert "--from-dir" in create.stdout
    assert "--from-files" in create.stdout

    send = run_cli(["wallet", "send", "--help"], env)
    assert send.returncode == 0, send.stderr
    assert "ASSET" in send.stdout
    assert "USDC" not in send.stdout
    assert "TO" in send.stdout
    assert "AMOUNT" in send.stdout

    wallet = run_cli(["wallet", "--help"], env)
    assert wallet.returncode == 0, wallet.stderr
    assert "authorize --file PATH" in wallet.stdout
    assert "wallet send" in wallet.stdout

    auth = run_cli(["wallet", "authorize", "--help"], env)
    assert auth.returncode == 0, auth.stderr
    assert "--file" in auth.stdout
    assert "signature to attach" in auth.stdout
    assert "not a send" in auth.stdout
    assert "typed statement" in auth.stdout
    assert "--print" not in auth.stdout

    email_send = run_cli(["email", "send", "--help"], env)
    assert email_send.returncode == 0, email_send.stderr
    assert "backends email" in email_send.stdout

    email = run_cli(["email", "--help"], env)
    assert email.returncode == 0, email.stderr
    for verb in ("connect", "show", "send", "receive", "list"):
        assert verb in email.stdout, verb

    init = run_cli(["init", "--help"], env)
    assert init.returncode == 0, init.stderr
    assert "--email" in init.stdout
    assert "--wallet" in init.stdout
    assert "--store" in init.stdout
    assert "--wallet-key-file" in init.stdout
    assert "base (default)" in init.stdout
    assert "agentmail (default)" in init.stdout


def test_operation_help_stays_provider_neutral(tmp_path):
    env = cli_env(tmp_path / "vault")
    provider_names = {
        name.lower() for channel in CHANNELS.values() for name in channel.names
    }
    for args in _OPERATION_HELPS:
        proc = run_cli(list(args), env)
        assert proc.returncode == 0, (args, proc.stderr)
        text = proc.stdout + proc.stderr
        for provider in provider_names:
            assert not _has_token(text, provider), (args, provider, text)


def test_backend_email_discovery_includes_first_run_stop_rules(tmp_path):
    proc = run_cli(["backends", "email", "agentmail"], cli_env(tmp_path / "vault"))
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    data = json.loads(proc.stdout)
    options = data["channel"]["backends"][0]["options"]
    helps = " ".join(str(item.get("help") or "") for item in options)
    names = [item["name"] for item in options]
    assert "credential" in names
    assert "--result-file" in helps
    assert "claimed, forbidden, or unavailable" in helps
    assert "stop and ask the user" in helps


def test_help_does_not_print_status(tmp_path):
    env = cli_env(tmp_path / "vault")
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr
    addr = json.loads(started.stdout)["address"]
    assert addr.startswith("0x")
    help_after = run_cli(["--help"], env)
    assert help_after.returncode == 0, help_after.stderr
    after = help_after.stdout + help_after.stderr
    assert "email: not configured" not in after.lower()
    assert addr not in after
    assert "age1" not in after.lower()
    assert json.loads(started.stdout)["ok"] is True

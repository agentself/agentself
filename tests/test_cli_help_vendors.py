"""Public CLI help: featured commands and flags exist; generic help stays provider-neutral."""

from __future__ import annotations

from tests.support import cli_env, run_cli

_FEATURED_TOP = (
    "init",
    "show",
    "backends",
    "diagnose",
    "secret",
    "email",
    "wallet",
    "backup",
    "restore",
    "install",
)


def test_top_help_lists_commands_and_flags(tmp_path):
    proc = run_cli(["--help"], cli_env(tmp_path / "vault"))
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout
    for verb in _FEATURED_TOP:
        assert verb in text, verb
    assert "--json" in text
    assert "--version" in text
    assert "AGENTSELF_IDENTITY_DIR" in text
    assert "0x" not in text


def test_nested_help_exposes_arguments_not_providers(tmp_path):
    env = cli_env(tmp_path / "vault")
    create = run_cli(["secret", "create", "--help"], env)
    assert create.returncode == 0, create.stderr
    assert "--json" in create.stdout
    assert "NAME" in create.stdout
    assert "VALUE" in create.stdout
    assert "--file" in create.stdout

    send = run_cli(["wallet", "send", "--help"], env)
    assert send.returncode == 0, send.stderr
    assert "ASSET" in send.stdout
    assert "USDC" not in send.stdout
    assert "TO" in send.stdout
    assert "AMOUNT" in send.stdout

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
    assert "base (default)" in init.stdout
    assert "agentmail (default)" in init.stdout


def test_help_does_not_print_status(tmp_path):
    env = cli_env(tmp_path / "vault")
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr
    addr = ""
    for line in started.stdout.splitlines():
        if line.startswith("wallet:"):
            addr = line.split(":", 1)[1].strip()
    assert addr.startswith("0x")
    help_after = run_cli(["--help"], env)
    assert help_after.returncode == 0, help_after.stderr
    after = help_after.stdout + help_after.stderr
    assert "email: not configured" not in after.lower()
    assert addr not in after
    assert "age1" not in after.lower()

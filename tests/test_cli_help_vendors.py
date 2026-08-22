"""CLI help must not contain vendor names."""

from __future__ import annotations

import re

from tests.support import CLI_HELPS, cli_env, run_cli

VENDORS = (
    "cloudflare",
    "twilio",
    "resend",
    "alby",
    "custodymanager",
    "resourceaccess",
    "volatility",
    "slice",
    "sops",
    "idesign",
    "seal",
    "reveal",
)

HELPS = CLI_HELPS

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
_ALIAS_TOP = ("start", "set", "change", "recv", "key", "sign", "doctor", "recipient")


def _has_token(text: str, token: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", text) is not None


def test_cli_help_has_no_vendor_names(tmp_path):
    env = cli_env(tmp_path / "vault")
    for args in HELPS:
        proc = run_cli(args, env)
        text = (proc.stdout + proc.stderr).lower()
        assert proc.returncode == 0, (args, proc.stderr)
        host_catalog = args and args[0] in ("init", "backends", "diagnose")
        for vendor in VENDORS:
            if vendor == "sops" and host_catalog:
                continue
            assert vendor not in text, f"{vendor} in help for {args}: {text}"
        if args == ["--help"]:
            for verb in _FEATURED_TOP:
                assert verb in text, f"{verb} missing from --help: {text}"
            for gone in ("seal", "reveal", "enroll"):
                assert gone not in text, f"{gone} in --help: {text}"
            for alias in _ALIAS_TOP:
                assert not _has_token(text, alias), (
                    f"{alias} featured in --help: {text}"
                )
            assert (
                "{init,show,backends,diagnose,secret,email,wallet,backup,restore,install}"
                in text
            )
        if args == ["wallet", "--help"]:
            for verb in ("show", "address", "balance", "authorize", "send", "verify"):
                assert verb in text, f"{verb} missing from wallet --help: {text}"
            assert "sign" not in text, f"sign featured in wallet --help: {text}"
            for word in ("base", "ethereum", "lit"):
                assert word not in text, f"{word} in wallet --help: {text}"
        if args == ["wallet", "address", "--help"]:
            assert "destination" in text, text
        if args == ["email", "--help"]:
            for verb in ("connect", "show", "send", "receive", "list"):
                assert verb in text, f"{verb} missing from email --help: {text}"
            assert not _has_token(text, "set"), f"set featured in email --help: {text}"
            assert not _has_token(text, "recv"), (
                f"recv featured in email --help: {text}"
            )
            assert "{connect,show,send,receive,list}" in text
        if args == ["email", "receive", "--help"]:
            assert "receiv" in text, text
            assert "fetch" in text, text
            assert "id" in text, text


def test_top_help_teaches_discovery(tmp_path):
    proc = run_cli(["--help"], cli_env(tmp_path / "vault"))
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout
    assert "agentself <command> --help" in text
    assert "--json" in text
    assert "Examples:" in text
    assert "--version" in text
    assert "0 ok" in text
    assert "1 error" in text
    assert "2 refused" in text
    assert "3 missing" in text
    assert "AGENTSELF_VAULT_ROOT" in text
    assert "See the README" not in text
    assert "email: not configured" not in text.lower()
    assert "0x" not in text


def test_nested_help_shows_args_and_defaults(tmp_path):
    env = cli_env(tmp_path / "vault")
    create = run_cli(["secret", "create", "--help"], env)
    assert create.returncode == 0, create.stderr
    assert "--json" in create.stdout
    assert "NAME" in create.stdout
    assert "VALUE" in create.stdout
    assert "--file" in create.stdout
    assert "Refuses if the name exists" in create.stdout

    send = run_cli(["wallet", "send", "--help"], env)
    assert send.returncode == 0, send.stderr
    assert "USDC" in send.stdout
    assert "TO" in send.stdout
    assert "AMOUNT" in send.stdout

    email_send = run_cli(["email", "send", "--help"], env)
    assert email_send.returncode == 0, email_send.stderr
    assert "fails closed" in email_send.stdout.lower()
    assert "backends email" in email_send.stdout
    assert "agentmail" not in email_send.stdout.lower()
    assert "imap" not in email_send.stdout.lower()

    sms = run_cli(["sms", "--help"], env)
    assert sms.returncode == 2, sms.stdout + sms.stderr

    init = run_cli(["init", "--help"], env)
    assert init.returncode == 0, init.stderr
    assert "does not block init" in init.stdout
    assert "base (default)" in init.stdout
    assert "agentmail (default)" in init.stdout
    assert "--email" in init.stdout
    assert "--mailbox" not in init.stdout
    assert "SMS bind:" not in init.stdout


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
    assert "usage" in after.lower()
    assert "email: not configured" not in after.lower()
    assert addr not in after
    assert "age1" not in after.lower()

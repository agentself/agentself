"""Raw bytes, hidden --json identity, and no implicit stdin."""

from __future__ import annotations

import json

from tests.support import cli_env, run_cli, value_file


def _init(tmp_path):
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["init"], env)
    assert proc.returncode == 0, proc.stderr
    return env, json.loads(proc.stdout)


def test_hidden_json_is_byte_identical(tmp_path):
    env, _started = _init(tmp_path)
    default = run_cli(["show"], env)
    before = run_cli(["--json", "show"], env)
    after = run_cli(["show", "--json"], env)
    assert default.returncode == 0
    assert default.stderr == before.stderr == after.stderr == ""
    assert default.stdout == before.stdout == after.stdout
    assert default.stdout.endswith("\n")
    assert json.loads(default.stdout)["ok"] is True


def test_raw_wallet_address_and_show_are_exact(tmp_path):
    env, started = _init(tmp_path)
    addr = started["address"]
    for args in (["wallet", "address", "--raw"], ["--raw", "wallet", "show"]):
        proc = run_cli(args, env)
        assert proc.returncode == 0, proc.stderr
        assert proc.stderr == ""
        assert proc.stdout == addr
        assert not proc.stdout.endswith("\n")


def test_raw_secret_and_note_are_exact(tmp_path):
    env, _started = _init(tmp_path)
    secret = "raw-secret"
    assert (
        run_cli(
            ["secret", "create", "demo.token", "--file", value_file(tmp_path, secret)],
            env,
        ).returncode
        == 0
    )
    got = run_cli(["secret", "get", "demo.token", "--raw"], env)
    assert got.returncode == 0
    assert got.stdout == secret
    assert run_cli(["note", "set", "handoff", "note-body"], env).returncode == 0
    note = run_cli(["note", "get", "handoff", "--raw"], env)
    assert note.returncode == 0
    assert note.stdout == "note-body"


def test_raw_wallet_authorize_is_exact(tmp_path):
    env, _started = _init(tmp_path)
    message = value_file(tmp_path, "hello", "msg.txt")
    js = run_cli(["wallet", "authorize", "--file", message], env)
    assert js.returncode == 0, js.stderr
    token = json.loads(js.stdout)["authorization"]
    raw = run_cli(["wallet", "authorize", "--file", message, "--raw"], env)
    assert raw.returncode == 0
    assert raw.stdout == token
    assert not raw.stdout.endswith("\n")


def test_unsupported_raw_and_conflicts(tmp_path):
    env, _started = _init(tmp_path)
    refused = run_cli(["show", "--raw"], env)
    assert refused.returncode == 2
    assert refused.stderr == ""
    data = json.loads(refused.stdout)
    assert data["error"] == "refused"
    assert data["reason"] == "--raw is not supported"
    dest = tmp_path / "out.txt"
    clash = run_cli(
        ["secret", "get", "wallet.key", "--raw", "--file", str(dest), "--unsafe"],
        env,
    )
    assert clash.returncode == 2
    assert json.loads(clash.stdout)["reason"] == "--raw cannot be used with --file"
    meta = run_cli(["secret", "get", "wallet.key", "--raw", "--meta"], env)
    assert meta.returncode == 2
    assert json.loads(meta.stdout)["reason"] == "--raw cannot be used with --meta"


def test_email_receive_raw_requires_ref(tmp_path):
    env, _started = _init(tmp_path)
    missing = run_cli(["email", "receive", "--raw"], env)
    assert missing.returncode == 2
    assert missing.stderr == ""
    assert json.loads(missing.stdout)["reason"] == "--raw requires a message ref or ID"


def test_protected_secret_raw_checks_before_get(tmp_path):
    env, _started = _init(tmp_path)
    refused = run_cli(["secret", "get", "wallet.key", "--raw"], env)
    assert refused.returncode == 2
    assert json.loads(refused.stdout)["reason"] == "wallet.key is protected"
    meta = run_cli(["secret", "get", "wallet.key", "--meta"], env)
    assert meta.returncode == 0
    assert "value" not in json.loads(meta.stdout)


def test_unexpected_stdin_is_ignored(tmp_path):
    env, _started = _init(tmp_path)
    created = run_cli(
        ["secret", "create", "demo.token", "argv-secret"],
        env,
        input="piped-secret\n",
    )
    assert created.returncode == 0, created.stderr
    got = run_cli(["secret", "get", "demo.token"], env)
    assert json.loads(got.stdout)["value"] == "argv-secret"
    missing = run_cli(["secret", "create", "other"], env, input="piped-secret\n")
    assert missing.returncode == 3
    assert json.loads(missing.stdout)["reason"] == "need a value"
    explicit = run_cli(
        ["secret", "create", "piped", "--file", "-"],
        env,
        input="piped-secret\n",
    )
    assert explicit.returncode == 0
    assert (
        json.loads(run_cli(["secret", "get", "piped"], env).stdout)["value"].rstrip(
            "\r\n"
        )
        == "piped-secret"
    )

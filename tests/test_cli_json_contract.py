"""Public agentself --json contract. One object. Frozen keys. No schema drift."""

from __future__ import annotations

import json
from pathlib import Path

from agentself import __version__
from agentself.cli.app import main
from agentself.cli.parser import _FEATURED, _parser
from agentself.host import CHANNELS
from agentself.internal.files import identity_home

from tests.support import (
    MockRpc,
    apply_cli_env,
    cli_env,
    compose_with_rpc,
    run_cli,
)

GOLDENS = Path(__file__).resolve().parent / "goldens" / "json"
COMMANDS = json.loads((GOLDENS / "commands.json").read_text(encoding="utf-8"))
SUCCESS_KEYS = json.loads((GOLDENS / "success_keys.json").read_text(encoding="utf-8"))
ERROR_GOLDEN = json.loads((GOLDENS / "error_envelope.json").read_text(encoding="utf-8"))

ERROR_ENVELOPE = tuple(ERROR_GOLDEN["keys"])
ERROR_TOKENS = frozenset(ERROR_GOLDEN["error"])
EXIT_FOR_ERROR = {key: int(value) for key, value in ERROR_GOLDEN["exit"].items()}
DIAGNOSE_EXTRA = frozenset(ERROR_GOLDEN["diagnose_extra"])
SETUP_EXTRA = frozenset(ERROR_GOLDEN.get("setup_extra", []))
CANARIES = ("AGE-SECRET-KEY", "hold-token-CANARY", "plain-secret-CANARY")
_TO = "0x" + "11" * 20


def _golden(name: str):
    return json.loads((GOLDENS / name).read_text(encoding="utf-8"))


def _spec(name: str) -> dict:
    return SUCCESS_KEYS[name]


def _assert_keys(data: dict, spec: dict) -> None:
    got = set(data)
    if "exact" in spec:
        assert got == set(spec["exact"]), (sorted(got), sorted(spec["exact"]))
        if "ok" in spec["exact"]:
            assert data.get("ok") is True or data.get("ok") is False
    if "required" in spec:
        missing = set(spec["required"]) - got
        assert not missing, missing
    if "allowed" in spec:
        extra = got - set(spec["allowed"])
        assert not extra, extra


def _assert_clean(blob: str) -> None:
    for canary in CANARIES:
        assert canary not in blob
    assert "Traceback" not in blob


def _one_object(text: str) -> dict:
    assert text.endswith("\n"), text
    assert text.count("\n") == 1, text
    data = json.loads(text)
    assert isinstance(data, dict), data
    return data


def assert_ok(proc, spec_name: str) -> dict:
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == "", proc.stderr
    _assert_clean(proc.stdout + proc.stderr)
    data = _one_object(proc.stdout)
    assert data["ok"] is True
    _assert_keys(data, _spec(spec_name))
    return data


def assert_err(proc, *, error: str | None = None) -> dict:
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert proc.stderr == "", proc.stderr
    _assert_clean(proc.stdout + proc.stderr)
    data = _one_object(proc.stdout)
    assert data["ok"] is False
    missing = set(ERROR_ENVELOPE) - set(data)
    assert not missing, (missing, data)
    extra = set(data) - set(ERROR_ENVELOPE) - DIAGNOSE_EXTRA - SETUP_EXTRA
    assert not extra, extra
    assert data["error"] in ERROR_TOKENS
    assert isinstance(data["reason"], str) and data["reason"]
    assert isinstance(data["next"], str) and data["next"].startswith("agentself ")
    if error is not None:
        assert data["error"] == error
        assert proc.returncode == EXIT_FOR_ERROR[error], proc.returncode
    else:
        assert proc.returncode == EXIT_FOR_ERROR[data["error"]], proc.returncode
    return data


def _init(tmp_path, extra: list[str] | None = None, env_extra: dict | None = None):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    if env_extra:
        env.update(env_extra)
    args = ["--json", "init"]
    if extra:
        args.extend(extra)
    proc = run_cli(args, env)
    data = assert_ok(proc, "init")
    return vault, env, data


def _main_ok(monkeypatch, capsys, env, argv, spec_name: str) -> dict:
    apply_cli_env(monkeypatch, env)
    code = main(["--json", *argv])
    captured = capsys.readouterr()
    proc = type(
        "P", (), {"returncode": code, "stdout": captured.out, "stderr": captured.err}
    )
    return assert_ok(proc, spec_name)


def _main_err(monkeypatch, capsys, env, argv, *, error: str | None = None) -> dict:
    apply_cli_env(monkeypatch, env)
    code = main(["--json", *argv])
    captured = capsys.readouterr()
    proc = type(
        "P", (), {"returncode": code, "stdout": captured.out, "stderr": captured.err}
    )
    return assert_err(proc, error=error)


def test_error_envelope_golden_matches_readme_exit_codes():
    assert ERROR_GOLDEN["ok"] is False
    assert ERROR_GOLDEN["streams"] == {"success": "stdout", "failure": "stdout"}
    assert EXIT_FOR_ERROR == {"error": 1, "refused": 2, "missing": 3}
    assert set(SUCCESS_KEYS) >= {
        "version",
        "init",
        "show",
        "diagnose",
        "install",
        "backends",
        "secret_write",
        "secret_get",
        "email_connect",
        "email_send",
        "wallet_address",
        "backup",
    }


def test_featured_parser_matches_golden_commands():
    parser = _parser()
    assert _FEATURED == "{" + ",".join(COMMANDS["top"]) + "}"
    action = next(item for item in parser._actions if item.dest == "command")
    featured = [item.dest for item in action._choices_actions]
    assert featured == COMMANDS["top"]
    subs = action.choices
    for group, dest in (
        ("secret", "secret_command"),
        ("note", "note_command"),
        ("email", "email_command"),
        ("wallet", "wallet_command"),
    ):
        inner = next(item for item in subs[group]._actions if item.dest == dest)
        shown = [item.dest for item in inner._choices_actions]
        assert shown == COMMANDS[group], (group, shown)


def test_json_version_and_machine_alias(tmp_path):
    env = cli_env(tmp_path / "vault")
    version = assert_ok(run_cli(["--json", "--version"], env), "version")
    assert version["version"] == __version__
    assert version["cli"] == 3
    assert version["package"]
    assert version["executable"]
    machine = run_cli(["--machine", "--version"], env)
    assert machine.returncode == 2


def test_json_backends_match_goldens(tmp_path):
    env = cli_env(tmp_path / "vault")
    catalog = assert_ok(run_cli(["--json", "backends"], env), "backends")
    assert catalog == _golden("backends.json")
    for channel in CHANNELS:
        one = assert_ok(
            run_cli(["--json", "backends", channel], env), "backends_channel"
        )
        assert one == _golden(f"backends-{channel}.json")
        assert one["channel"]["name"] == channel
        assert "backends" in one["channel"]
        assert one["channel"]["backends"]


def test_json_init_show_identity_doctor_recipient(tmp_path):
    vault, env, started = _init(tmp_path)
    assert started["id"] == "agent"
    assert str(started["recipient"]).startswith("age1")
    assert str(started["address"]).startswith("0x")
    assert started["usdc"] == started["address"]
    assert started["wallet_backend"] == "base"
    assert started["email_backend"] == "agentmail"

    shown = assert_ok(run_cli(["--json", "show"], env), "show")
    assert shown["id"] == started["id"]
    assert shown["address"] == started["address"]
    assert shown["vault"] == str(vault)
    assert shown["recipient"] == started["recipient"]

    bare = assert_ok(run_cli(["--json"], env), "show")
    assert bare == shown

    diagnose = assert_ok(run_cli(["--json", "diagnose"], env), "diagnose")
    assert diagnose["initialized"] is True
    assert diagnose["vault"] == str(vault)
    assert diagnose["store_backend"] == "sops"
    assert diagnose["tools"]["age-keygen"] is True
    assert diagnose["tools"]["sops"] is True
    assert diagnose["ready"]["wallet"] is True
    assert diagnose["ready"]["email"] is False
    assert diagnose["ready"]["store"] is True


def test_json_secrets_and_missing(tmp_path):
    _vault, env, _started = _init(tmp_path)
    created = assert_ok(
        run_cli(["--json", "secret", "create", "notes", "only I can open this"], env),
        "secret_write",
    )
    assert created == {"ok": True, "name": "notes"}
    same = assert_ok(
        run_cli(["--json", "secret", "create", "notes", "only I can open this"], env),
        "secret_write",
    )
    assert same == {"ok": True, "name": "notes", "unchanged": True}
    missing_exists = assert_err(
        run_cli(["--json", "secret", "exists", "ghost"], env), error="missing"
    )
    assert missing_exists["exists"] is False
    present = assert_ok(
        run_cli(["--json", "secret", "exists", "notes"], env), "secret_exists"
    )
    assert present == {"ok": True, "name": "notes", "exists": True}
    got = assert_ok(run_cli(["--json", "secret", "get", "notes"], env), "secret_get")
    assert got == {"ok": True, "name": "notes", "value": "only I can open this"}
    updated = assert_ok(
        run_cli(["--json", "secret", "update", "notes", "rotated"], env),
        "secret_write",
    )
    assert updated == {"ok": True, "name": "notes"}
    listed = assert_ok(run_cli(["--json", "secret", "list"], env), "secret_list")
    assert "notes" in listed["names"]
    assert "wallet.key" in listed["protected"]
    assert "only I can open this" not in json.dumps(listed)
    missing = assert_err(
        run_cli(["--json", "secret", "get", "ghost"], env), error="missing"
    )
    assert missing["next"] == "agentself secret list"
    clash = assert_err(
        run_cli(["--json", "secret", "create", "notes", "nope"], env),
        error="refused",
    )
    assert "next" in clash
    deleted = assert_ok(
        run_cli(["--json", "secret", "delete", "notes"], env),
        "secret_write",
    )
    assert deleted == {"ok": True, "name": "notes"}
    listed = assert_ok(run_cli(["--json", "secret", "list"], env), "secret_list")
    assert "notes" not in listed["names"]
    missing_del = assert_err(
        run_cli(["--json", "secret", "delete", "ghost"], env), error="missing"
    )
    assert missing_del["next"] == "agentself secret list"
    protected = assert_err(
        run_cli(["--json", "secret", "delete", "wallet.key"], env), error="refused"
    )
    assert "protected" in protected["reason"]
    assert protected["next"] == "agentself secret list"


def test_json_email_connect_without_token_is_missing(tmp_path):
    _vault, env, _started = _init(tmp_path)
    connected = assert_err(
        run_cli(["--json", "email", "connect"], env), error="missing"
    )
    assert connected["status"] == "input_required"
    assert connected["setup_id"]
    assert connected["continue"].startswith("agentself email connect --continue ")
    assert connected["human_action_required"] is False
    names = [item["name"] for item in connected["options"]]
    assert "credential" in names
    shown = assert_err(run_cli(["--json", "email", "show"], env), error="missing")
    assert shown["reason"] == "not configured"
    assert shown["next"] == "agentself email connect"


def test_json_wallet_address_and_injected_balance_send(tmp_path, monkeypatch, capsys):
    _vault, env, started = _init(tmp_path)
    addr = assert_ok(run_cli(["--json", "wallet", "address"], env), "wallet_address")
    assert addr == {"ok": True, "address": started["address"]}
    shown = assert_ok(run_cli(["--json", "wallet", "show"], env), "wallet_address")
    assert shown == addr

    compose_with_rpc(monkeypatch, MockRpc(eth_wei=10**18, usdc_raw=1_500_000))
    bal = _main_ok(monkeypatch, capsys, env, ["wallet", "balance"], "wallet_balance")
    assert bal["asset"] == "USDC"
    assert bal["amount"] == "1.5"
    assert "private_key" not in bal
    auth = _main_ok(
        monkeypatch, capsys, env, ["wallet", "authorize", "hello"], "wallet_authorize"
    )
    assert auth["authorization"].startswith("0x")
    assert auth["signature"] == auth["authorization"]
    assert auth["address"] == started["address"]
    assert auth["scheme"] == "eip191"
    assert auth["network"] == "base"
    assert len(auth["message_sha256"]) == 64
    sent = _main_ok(
        monkeypatch,
        capsys,
        env,
        ["wallet", "send", _TO, "1"],
        "wallet_send",
    )
    assert sent == {"ok": True, "to": _TO, "amount": "1", "asset": "USDC"}


def test_json_failure_envelope_and_streams(tmp_path):
    env = cli_env(tmp_path / "vault")
    unbound = assert_err(run_cli(["--json", "show"], env), error="refused")
    assert unbound["reason"] == "not initialized"
    assert unbound["next"] == "agentself init"

    usage = assert_err(run_cli(["--json", "secret"], env), error="refused")
    assert usage["next"] == "agentself secret --help"
    create_usage = assert_err(
        run_cli(["--json", "secret", "create"], env), error="refused"
    )
    assert create_usage["next"] == "agentself secret create --help"

    unknown = assert_err(run_cli(["--json", "ninit"], env), error="refused")
    assert unknown["next"] == "agentself --help"
    assert "did you mean 'init'" in unknown["reason"]
    assert "'start'" not in unknown["reason"]

    vault, env, _started = _init(tmp_path)
    send = assert_err(
        run_cli(
            ["--json", "email", "send", "a@example.com", "subj", "body"],
            env,
        ),
        error="error",
    )
    assert send["reason"] == "not_ready"
    assert send["next"] == "agentself backends email"
    outbox = identity_home(vault, "agent") / "outbox"
    assert not outbox.exists() or not list(outbox.iterdir())

    bind = assert_err(
        run_cli(["--json", "init", "--wallet", "nope"], env), error="refused"
    )
    assert "unknown wallet backend: nope" in bind["reason"]
    assert bind["next"] == "agentself backends wallet"

    skills = assert_err(
        run_cli(["--json", "install", "--skills=nope"], env, cwd=tmp_path),
        error="refused",
    )
    assert skills["next"] == "agentself install --help"


def test_json_install_and_doctor_fresh(tmp_path):
    env = cli_env(tmp_path / "vault")
    diagnose = assert_ok(run_cli(["--json", "diagnose"], env), "diagnose")
    assert diagnose["initialized"] is False
    assert diagnose["wallet_backend"] is None
    assert diagnose["ready"]["email"] is False
    installed = assert_ok(
        run_cli(["--json", "install", "--skills=agents", "--local"], env, cwd=tmp_path),
        "install",
    )
    assert len(installed["paths"]) == 1
    assert installed["paths"][0].endswith("SKILL.md")


def test_json_doctor_identity_problem_keeps_envelope(tmp_path):
    vault, env, _started = _init(tmp_path)
    cfg = json.loads((vault / "config.json").read_text(encoding="utf-8"))
    cfg["wallet_backend"] = "basee"
    (vault / "config.json").write_text(json.dumps(cfg) + "\n", encoding="utf-8")
    proc = run_cli(["--json", "diagnose"], env)
    data = assert_err(proc, error="error")
    assert data["initialized"] is True
    assert data["wallet_backend"] == "basee"
    assert "problems" in data
    assert data["next"] == "agentself backends wallet"

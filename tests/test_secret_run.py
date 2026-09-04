"""secret run injects store values into one child process without leaking them."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from tests.support import cli_env, run_cli, value_file

CANARY = "one-shot-CANARY-must-not-escape"


def _init(tmp_path: Path) -> dict[str, str]:
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["init"], env)
    assert proc.returncode == 0, proc.stderr
    return env


def _create(env: dict[str, str], tmp_path: Path, name: str, value: str) -> None:
    created = run_cli(
        [
            "secret",
            "create",
            name,
            "--file",
            value_file(tmp_path, value, name + ".txt"),
        ],
        env,
    )
    assert created.returncode == 0, created.stderr
    assert value not in created.stdout + created.stderr


def _hash_cmd(var: str = "API_KEY") -> list[str]:
    return [
        sys.executable,
        "-c",
        "import hashlib, os, sys\n"
        f"v = os.environ.get({var!r}, '')\n"
        "sys.stdout.write(hashlib.sha256(v.encode()).hexdigest())\n",
    ]


def _echo_cmd(var: str = "API_KEY") -> list[str]:
    return [
        sys.executable,
        "-c",
        "import os, sys\n"
        f"sys.stdout.write('pre ' + os.environ.get({var!r}, '') + ' post')\n",
    ]


def test_secret_run_injects_env_and_redacts_success_json(tmp_path: Path) -> None:
    env = _init(tmp_path)
    _create(env, tmp_path, "demo.token", CANARY)
    digest = hashlib.sha256(CANARY.encode()).hexdigest()
    ran = run_cli(
        ["secret", "run", "--env", "API_KEY=demo.token", "--", *_hash_cmd()],
        env,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert ran.stderr == ""
    blob = ran.stdout + ran.stderr
    assert CANARY not in blob
    payload = json.loads(ran.stdout)
    assert payload["ok"] is True
    assert payload["exit"] == 0
    assert payload["names"] == ["demo.token"]
    assert payload["env"] == ["API_KEY"]
    assert payload["stdout"] == digest
    assert payload["stderr"] == ""
    assert "value" not in payload
    got = run_cli(["secret", "get", "demo.token"], env)
    assert got.returncode == 0
    assert json.loads(got.stdout)["value"] == CANARY


def test_secret_run_redacts_child_echo_of_the_secret(tmp_path: Path) -> None:
    env = _init(tmp_path)
    _create(env, tmp_path, "demo.token", CANARY)
    ran = run_cli(
        ["secret", "run", "--env", "API_KEY=demo.token", "--", *_echo_cmd()],
        env,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert ran.stderr == ""
    assert CANARY not in ran.stdout + ran.stderr
    payload = json.loads(ran.stdout)
    assert payload["stdout"] == "pre [redacted] post"
    raw = run_cli(["secret", "get", "demo.token", "--raw"], env)
    assert raw.returncode == 0
    assert raw.stdout == CANARY


def test_secret_run_keeps_get_file_and_raw(tmp_path: Path) -> None:
    env = _init(tmp_path)
    _create(env, tmp_path, "demo.token", CANARY)
    dest = tmp_path / "exported.txt"
    wrote = run_cli(["secret", "get", "demo.token", "--file", str(dest)], env)
    assert wrote.returncode == 0, wrote.stderr
    assert dest.read_text(encoding="utf-8") == CANARY
    assert CANARY not in wrote.stdout + wrote.stderr
    raw = run_cli(["secret", "get", "demo.token", "--raw"], env)
    assert raw.stdout == CANARY
    listed = run_cli(["secret", "list"], env)
    assert listed.returncode == 0
    assert CANARY not in listed.stdout + listed.stderr


def test_secret_run_multiple_env_and_nonzero_child_exit(tmp_path: Path) -> None:
    env = _init(tmp_path)
    _create(env, tmp_path, "demo.token", CANARY)
    _create(env, tmp_path, "other.token", "second-CANARY-must-not-escape")
    script = (
        "import os, sys\n"
        "ok = os.environ.get('API_KEY') and os.environ.get('OTHER')\n"
        "sys.exit(0 if ok else 4)\n"
    )
    ran = run_cli(
        [
            "secret",
            "run",
            "--env",
            "API_KEY=demo.token",
            "--env",
            "OTHER=other.token",
            "--",
            sys.executable,
            "-c",
            script,
        ],
        env,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    payload = json.loads(ran.stdout)
    assert payload["exit"] == 0
    assert payload["names"] == ["demo.token", "other.token"]
    assert payload["env"] == ["API_KEY", "OTHER"]
    assert CANARY not in ran.stdout
    assert "second-CANARY-must-not-escape" not in ran.stdout
    failed = run_cli(
        [
            "secret",
            "run",
            "--env",
            "API_KEY=demo.token",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(7)",
        ],
        env,
    )
    assert failed.returncode == 0, failed.stdout + failed.stderr
    assert json.loads(failed.stdout)["exit"] == 7
    assert CANARY not in failed.stdout + failed.stderr


def test_secret_run_refuses_protected_and_missing(tmp_path: Path) -> None:
    env = _init(tmp_path)
    protected = run_cli(
        ["secret", "run", "--env", "KEY=wallet.key", "--", *_hash_cmd("KEY")],
        env,
    )
    assert protected.returncode == 2
    assert protected.stderr == ""
    data = json.loads(protected.stdout)
    assert data["error"] == "refused"
    assert "protected" in data["reason"]
    hexkey = json.loads(
        run_cli(["secret", "get", "wallet.key", "--unsafe"], env).stdout
    )["value"]
    assert hexkey not in protected.stdout
    missing = run_cli(
        ["secret", "run", "--env", "API_KEY=ghost.token", "--", *_hash_cmd()],
        env,
    )
    assert missing.returncode == 3
    miss = json.loads(missing.stdout)
    assert miss["error"] == "missing"
    assert miss["next"] == "agentself secret list"


def test_secret_run_usage_errors_stay_closed(tmp_path: Path) -> None:
    env = _init(tmp_path)
    _create(env, tmp_path, "demo.token", CANARY)
    cases = (
        (["secret", "run"], "need VAR=NAME"),
        (["secret", "run", "--env", "API_KEY=demo.token"], "need a command"),
        (["secret", "run", "--env", "not-a-binding"], "need VAR=NAME"),
        (["secret", "run", "--env", "1BAD=demo.token"], "need VAR=NAME"),
        (
            [
                "secret",
                "run",
                "--env",
                "API_KEY=demo.token",
                "--env",
                "API_KEY=demo.token",
            ],
            "duplicate env",
        ),
    )
    for args, reason in cases:
        proc = run_cli(args, env)
        assert proc.returncode == 2, (args, proc.stdout)
        assert proc.stderr == ""
        data = json.loads(proc.stdout)
        assert data["error"] == "refused"
        assert data["reason"] == reason
        assert data["next"] == "agentself secret run --help"
        assert CANARY not in proc.stdout


def test_secret_run_unknown_command_is_error(tmp_path: Path) -> None:
    env = _init(tmp_path)
    _create(env, tmp_path, "demo.token", CANARY)
    proc = run_cli(
        [
            "secret",
            "run",
            "--env",
            "API_KEY=demo.token",
            "--",
            "agentself-missing-child-3f57",
        ],
        env,
    )
    assert proc.returncode == 1
    assert proc.stderr == ""
    data = json.loads(proc.stdout)
    assert data["error"] == "error"
    assert data["reason"] == "command"
    assert CANARY not in proc.stdout + proc.stderr

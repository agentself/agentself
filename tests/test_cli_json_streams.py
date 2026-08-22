"""CLI schema 3: --json results (including failures) go to stdout."""

from __future__ import annotations

import json
from pathlib import Path

from tests.support import cli_env, run_cli


def test_json_failure_uses_stdout_and_empty_stderr(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0
    proc = run_cli(["--json", "email", "connect"], env)
    assert proc.returncode == 3
    assert proc.stderr == ""
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "missing"
    assert payload["status"] == "input_required"
    assert payload["next"].startswith("agentself email connect --continue")


def test_json_argparse_failure_uses_stdout(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["--json", "secret", "get"], env)
    assert proc.returncode == 2
    assert proc.stderr == ""
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "refused"
    assert payload["next"].startswith("agentself ")


def test_json_success_uses_stdout(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["--json", "init"], env)
    assert proc.returncode == 0
    assert proc.stderr == ""
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["id"] == "agent"
    assert str(payload["address"]).startswith("0x")

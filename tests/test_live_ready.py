"""Live-ready product gaps: missing sops after init."""

from __future__ import annotations

import json
from pathlib import Path

from tests.support import cli_env, plant_host_binaries, run_cli, value_file


def _path_with_age_without_sops(tmp_path: Path) -> str:
    return str(plant_host_binaries(tmp_path / "host-bin", "age-keygen", "age"))


def test_set_missing_sops_one_error_line(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr

    env = dict(env)
    env["PATH"] = _path_with_age_without_sops(tmp_path)
    proc = run_cli(
        ["secret", "create", "notes", "--file", value_file(tmp_path, "x")], env
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr
    assert "Traceback" not in proc.stdout
    lines = [line for line in proc.stderr.splitlines() if line.strip()]
    assert lines[0] == "error: sops not on PATH"
    assert "install --tools" in proc.stderr


def test_set_missing_sops_json_error(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr

    env = dict(env)
    env["PATH"] = _path_with_age_without_sops(tmp_path)
    proc = run_cli(
        [
            "secret",
            "create",
            "--json",
            "notes",
            "--file",
            value_file(tmp_path, "x"),
        ],
        env,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    err = json.loads(proc.stdout or proc.stderr)
    assert err == {
        "ok": False,
        "error": "error",
        "reason": "sops not on PATH",
        "next": "agentself install --tools",
    }

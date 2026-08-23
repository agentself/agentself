"""Live-ready product gaps: missing sops after init."""

from __future__ import annotations

import json

from tests.support import cli_env, plant_host_binaries, run_cli, value_file


def test_set_missing_sops_json_error(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr

    env = dict(env)
    env["PATH"] = str(plant_host_binaries(tmp_path / "host-bin", "age-keygen", "age"))
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

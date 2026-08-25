"""start without sops is one error line, not a traceback and not age."""

from __future__ import annotations

import json

from tests.support import cli_env, plant_host_binaries, run_cli


def test_start_missing_sops_one_error_line(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    env = dict(env)
    env["PATH"] = str(plant_host_binaries(tmp_path / "host-bin", "age-keygen", "age"))
    proc = run_cli(["init"], env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert proc.stderr == ""
    assert "Traceback" not in proc.stdout
    data = json.loads(proc.stdout)
    assert data["reason"] == "sops not on PATH"
    assert data["next"] == "agentself install --tools"

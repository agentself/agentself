"""start without sops is one error line, not a traceback and not age."""

from __future__ import annotations

from tests.support import cli_env, plant_host_binaries, run_cli


def test_start_missing_sops_one_error_line(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    env = dict(env)
    env["PATH"] = str(plant_host_binaries(tmp_path / "host-bin", "age-keygen", "age"))
    proc = run_cli(["init"], env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr
    assert "Traceback" not in proc.stdout
    lines = [line for line in proc.stderr.splitlines() if line.strip()]
    assert lines[0] == "error: sops not on PATH"
    assert "install --tools" in proc.stderr

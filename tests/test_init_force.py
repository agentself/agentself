"""init refuses identity/backend mutations unless --force is given."""

from __future__ import annotations

import json
from pathlib import Path

from tests.support import cli_env, run_cli


def test_repeated_init_is_idempotent_when_unchanged(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    first = json.loads(run_cli(["--json", "init"], env).stdout)
    assert first["ok"] is True
    address = first["address"]
    second = json.loads(run_cli(["--json", "init"], env).stdout)
    assert second["ok"] is True
    assert second["address"] == address
    assert second["email_backend"] == "agentmail"


def test_backend_change_requires_force(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    started = run_cli(["--json", "init"], env)
    assert started.returncode == 0, started.stderr
    refused = run_cli(["--json", "init", "--email", "imap"], env)
    assert refused.returncode == 2
    payload = json.loads(refused.stdout)
    assert payload["error"] == "refused"
    assert payload["next"] == "agentself init --force"
    forced = run_cli(["--json", "init", "--email", "imap", "--force"], env)
    assert forced.returncode == 0, forced.stderr
    assert json.loads(forced.stdout)["email_backend"] == "imap"

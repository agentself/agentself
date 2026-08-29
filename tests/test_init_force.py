"""init refuses identity/backend mutations unless --force is given."""

from __future__ import annotations

import json
from pathlib import Path

from tests.support import cli_env, run_cli


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


def test_force_id_refuses_a_second_identity_in_one_vault(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    started = run_cli(["--json", "init", "--id", "agent"], env)
    assert started.returncode == 0, started.stderr
    first = json.loads(started.stdout)
    refused = run_cli(["--json", "init", "--force", "--id", "other"], env)
    assert refused.returncode == 2
    payload = json.loads(refused.stdout)
    assert payload["error"] == "refused"
    assert payload["reason"] == "identity already initialized"
    assert payload["next"] == "agentself init"
    shown = json.loads(run_cli(["--json", "show"], env).stdout)
    assert shown["id"] == first["id"]
    assert shown["address"] == first["address"]
    identities = json.loads(
        (tmp_path / "vault" / "registry.json").read_text(encoding="utf-8")
    )["identities"]
    assert list(identities) == ["agent"]


def test_force_id_refuses_when_age_key_file_is_missing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    started = run_cli(["--json", "init", "--id", "agent"], env)
    assert started.returncode == 0, started.stderr
    cfg_path = vault / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.pop("age_key_file", None)
    cfg_path.write_text(json.dumps(cfg) + "\n", encoding="utf-8")
    refused = run_cli(["--json", "init", "--force", "--id", "other"], env)
    assert refused.returncode == 2
    payload = json.loads(refused.stdout)
    assert payload["reason"] == "identity already initialized"
    assert (
        "other"
        not in json.loads((vault / "registry.json").read_text(encoding="utf-8"))[
            "identities"
        ]
    )

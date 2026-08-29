"""init refuses identity/backend mutations unless --force is given."""

from __future__ import annotations

import json
from pathlib import Path

from agentself.local import ensure_age_key

from tests.support import cli_env, run_cli

_SECOND_IDENTITY_NEXT = "agentself --identity-dir PATH init"


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
    assert payload["next"] == _SECOND_IDENTITY_NEXT
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
    assert payload["next"] == _SECOND_IDENTITY_NEXT
    assert (
        "other"
        not in json.loads((vault / "registry.json").read_text(encoding="utf-8"))[
            "identities"
        ]
    )


def test_env_identity_id_cannot_switch_or_mint_a_second_wallet(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    started = run_cli(["--json", "init", "--id", "samevault"], env)
    assert started.returncode == 0, started.stderr
    first = json.loads(started.stdout)
    env["AGENTSELF_IDENTITY_ID"] = "peer"
    shown = json.loads(run_cli(["--json", "show"], env).stdout)
    assert shown["id"] == "samevault"
    assert shown["address"] == first["address"]
    diagnosed = json.loads(run_cli(["--json", "diagnose"], env).stdout)
    assert diagnosed["ok"] is True
    assert diagnosed["next"] != "agentself init"
    refused = run_cli(["--json", "init", "--force", "--id", "peer"], env)
    assert refused.returncode == 2
    payload = json.loads(refused.stdout)
    assert payload["reason"] == "identity already initialized"
    assert payload["next"] == _SECOND_IDENTITY_NEXT
    again = run_cli(["--json", "init"], env)
    assert again.returncode == 0, again.stderr
    repeated = json.loads(again.stdout)
    assert repeated["id"] == "samevault"
    assert repeated["address"] == first["address"]
    identities = json.loads((vault / "registry.json").read_text(encoding="utf-8"))[
        "identities"
    ]
    assert list(identities) == ["samevault"]
    assert not (vault / "identities" / "peer").exists()


def test_registry_identity_blocks_a_second_name_without_config_id(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    started = run_cli(["--json", "init", "--id", "samevault"], env)
    assert started.returncode == 0, started.stderr
    first = json.loads(started.stdout)
    cfg_path = vault / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.pop("identity_id", None)
    cfg_path.write_text(json.dumps(cfg) + "\n", encoding="utf-8")
    refused = run_cli(["--json", "init", "--force", "--id", "peer"], env)
    assert refused.returncode == 2
    payload = json.loads(refused.stdout)
    assert payload["reason"] == "identity already initialized"
    assert payload["next"] == _SECOND_IDENTITY_NEXT
    identities = json.loads((vault / "registry.json").read_text(encoding="utf-8"))[
        "identities"
    ]
    assert list(identities) == ["samevault"]
    recovered = json.loads(run_cli(["--json", "init"], env).stdout)
    assert recovered["id"] == "samevault"
    assert recovered["address"] == first["address"]


def test_mixed_identities_diagnose_does_not_recommend_init(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    started = run_cli(["--json", "init", "--id", "samevault"], env)
    assert started.returncode == 0, started.stderr
    first = json.loads(started.stdout)
    registry_path = vault / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    record = dict(registry["identities"]["samevault"])
    record["id"] = "peer"
    registry["identities"]["peer"] = record
    registry_path.write_text(json.dumps(registry) + "\n", encoding="utf-8")
    diagnosed = run_cli(["--json", "diagnose"], env)
    assert diagnosed.returncode == 1
    payload = json.loads(diagnosed.stdout)
    assert payload["reason"] == "identity directory has more than one identity"
    assert payload["next"] == _SECOND_IDENTITY_NEXT
    assert "agentself init" not in payload["next"]
    refused = run_cli(["--json", "init"], env)
    assert refused.returncode == 2
    assert json.loads(refused.stdout)["reason"] == "identity already initialized"
    shown = json.loads(run_cli(["--json", "show"], env).stdout)
    assert shown["id"] == "samevault"
    assert shown["address"] == first["address"]
    identities = json.loads(registry_path.read_text(encoding="utf-8"))["identities"]
    assert set(identities) == {"samevault", "peer"}


def test_leftover_age_key_blocks_a_second_name(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    started = run_cli(["--json", "init", "--id", "samevault"], env)
    assert started.returncode == 0, started.stderr
    first = json.loads(started.stdout)
    ensure_age_key(vault, "peer")
    refused = run_cli(["--json", "init", "--force", "--id", "peer"], env)
    assert refused.returncode == 2
    payload = json.loads(refused.stdout)
    assert payload["reason"] == "identity already initialized"
    assert payload["next"] == _SECOND_IDENTITY_NEXT
    shown = json.loads(run_cli(["--json", "show"], env).stdout)
    assert shown["id"] == "samevault"
    assert shown["address"] == first["address"]
    identities = json.loads((vault / "registry.json").read_text(encoding="utf-8"))[
        "identities"
    ]
    assert list(identities) == ["samevault"]

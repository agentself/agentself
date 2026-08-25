"""Identity config.json / registry.json format_version contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import agentself.internal.format as format_mod
from agentself.internal.format import read_format_version
from agentself.internal.log import MemoryLog
from agentself.internal.registry import (
    FileIdentityAccess,
    RegistryError,
)
from agentself.local import IdentityStateError, load_config, save_config

from tests.support import cli_env, run_cli

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vault"
FUTURE_CONFIG_MSG = "cannot read config.json: format_version 2 is newer than this CLI; upgrade agentself"
FUTURE_REGISTRY_MSG = (
    "cannot read registry.json: format_version 2 is newer than this CLI; "
    "upgrade agentself"
)
CANARY = "future-canary-keep"


def _plant(vault: Path, fixture: str, name: str) -> Path:
    dest = Path(vault) / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes((FIXTURES / fixture).read_bytes())
    return dest


def test_missing_format_version_is_never_current(monkeypatch):
    with pytest.raises(ValueError, match="format_version is missing"):
        read_format_version({})
    with pytest.raises(ValueError, match="format_version is missing"):
        read_format_version({"identity_id": "agent"})
    monkeypatch.setattr(format_mod, "CURRENT_FORMAT_VERSION", 3)
    with pytest.raises(ValueError, match="format_version is missing"):
        read_format_version({})


def test_missing_config_is_uninitialized(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    assert load_config(vault) == {}


def test_unversioned_config_fails_closed_and_is_not_rewritten(tmp_path):
    vault = tmp_path / "vault"
    path = _plant(vault, "config_unversioned.json", "config.json")
    original = path.read_bytes()
    with pytest.raises(IdentityStateError, match="format_version is missing"):
        load_config(vault)
    assert path.read_bytes() == original


def test_v1_config_is_the_current_saved_contract(tmp_path):
    vault = tmp_path / "vault"
    _plant(vault, "config_v1.json", "config.json")
    assert load_config(vault) == {
        "age_key_file": "identities/agent/agent.agekey",
        "identity_id": "agent",
    }


def test_config_and_registry_accept_utf8_bom(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["--json", "init"], env).returncode == 0
    for name in ("config.json", "registry.json"):
        path = vault / name
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    shown = run_cli(["--json", "show"], env)
    assert shown.returncode == 0, shown.stderr
    listed = run_cli(["--json", "secret", "list"], env)
    assert listed.returncode == 0, listed.stderr


def test_future_config_fails_closed_and_is_not_rewritten(tmp_path):
    vault = tmp_path / "vault"
    path = _plant(vault, "config_future.json", "config.json")
    original = path.read_bytes()
    with pytest.raises(IdentityStateError, match="format_version 2 is newer"):
        load_config(vault)
    assert path.read_bytes() == original


def test_string_config_version_fails_closed(tmp_path):
    vault = tmp_path / "vault"
    path = _plant(vault, "config_string_version.json", "config.json")
    original = path.read_bytes()
    with pytest.raises(IdentityStateError, match="format_version is not an integer"):
        load_config(vault)
    assert path.read_bytes() == original


def test_save_config_stamps_format_version(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    save_config(vault, {"identity_id": "agent"})
    written = json.loads((vault / "config.json").read_text(encoding="utf-8"))
    assert written == {"format_version": 1, "identity_id": "agent"}


def test_init_stamps_format_version_1(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    proc = run_cli(["init"], env)
    assert proc.returncode == 0, proc.stderr
    cfg = json.loads((vault / "config.json").read_text(encoding="utf-8"))
    assert cfg["format_version"] == 1
    assert cfg["identity_id"] == "agent"
    assert cfg["wallet_backend"] == "base"
    assert cfg["email_backend"] == "agentmail"
    assert cfg["age_key_file"] == "identities/agent/agent.agekey"
    assert "format_version" not in load_config(vault)
    assert (vault / "identities" / "agent" / "agent.agekey").is_file()
    registry = json.loads((vault / "registry.json").read_text(encoding="utf-8"))
    assert registry["format_version"] == 1
    assert "agent" in registry["identities"]


def test_init_does_not_wipe_future_registry(tmp_path):
    vault = tmp_path / "vault"
    path = _plant(vault, "registry_future.json", "registry.json")
    original = path.read_bytes()
    env = cli_env(vault)
    proc = run_cli(["init"], env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert FUTURE_REGISTRY_MSG in json.loads(proc.stdout)["reason"]
    assert CANARY not in proc.stdout + proc.stderr
    assert path.read_bytes() == original
    assert not (vault / "identities").exists()
    js = run_cli(["--json", "init"], env)
    assert js.returncode == 1
    assert js.stderr == ""
    data = json.loads(js.stdout)
    assert data["ok"] is False
    assert data["reason"] == FUTURE_REGISTRY_MSG
    assert data["next"] != "agentself init"
    assert CANARY not in js.stderr
    assert path.read_bytes() == original
    assert not (vault / "identities").exists()


def test_init_does_not_wipe_future_config(tmp_path):
    vault = tmp_path / "vault"
    path = _plant(vault, "config_future.json", "config.json")
    original = path.read_bytes()
    env = cli_env(vault)
    proc = run_cli(["init"], env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert FUTURE_CONFIG_MSG in json.loads(proc.stdout)["reason"]
    assert CANARY not in proc.stdout + proc.stderr
    assert path.read_bytes() == original
    js = run_cli(["--json", "init"], env)
    assert js.returncode == 1
    assert js.stderr == ""
    data = json.loads(js.stdout)
    assert data["ok"] is False
    assert data["reason"] == FUTURE_CONFIG_MSG
    assert CANARY not in js.stderr
    assert path.read_bytes() == original


def test_json_show_future_config_is_one_stderr_object(tmp_path):
    vault = tmp_path / "vault"
    path = _plant(vault, "config_future.json", "config.json")
    original = path.read_bytes()
    env = cli_env(vault)
    proc = run_cli(["--json", "show"], env)
    assert proc.returncode == 1
    assert proc.stderr == ""
    assert proc.stdout.endswith("\n")
    assert proc.stdout.count("\n") == 1
    data = json.loads(proc.stdout or proc.stderr)
    assert data["ok"] is False
    assert data["error"] == "error"
    assert data["reason"] == FUTURE_CONFIG_MSG
    assert data["next"].startswith("agentself ")
    assert CANARY not in proc.stderr
    assert path.read_bytes() == original


def test_unversioned_registry_fails_closed_and_is_not_rewritten(tmp_path):
    vault = tmp_path / "vault"
    path = _plant(vault, "registry_unversioned.json", "registry.json")
    original = path.read_bytes()
    with pytest.raises(RegistryError, match="format_version is missing"):
        FileIdentityAccess(vault, MemoryLog()).find("agent")
    assert path.read_bytes() == original


def test_v1_registry_is_the_current_saved_contract(tmp_path):
    vault = tmp_path / "vault"
    _plant(vault, "registry_v1.json", "registry.json")
    identity = FileIdentityAccess(vault, MemoryLog()).find("agent")
    assert identity is not None
    assert identity.id == "agent"
    assert identity.recipient == "age1example"
    assert identity.store_binding == "sops"


def test_future_registry_fails_closed_and_is_not_rewritten(tmp_path):
    vault = tmp_path / "vault"
    path = _plant(vault, "registry_future.json", "registry.json")
    original = path.read_bytes()
    access = FileIdentityAccess(vault, MemoryLog())
    with pytest.raises(RegistryError, match="format_version 2 is newer"):
        access.find("agent")
    assert path.read_bytes() == original
    with pytest.raises(RegistryError, match="format_version 2 is newer"):
        access.init("agent", "age1example", "sops")
    assert path.read_bytes() == original


def test_string_registry_version_fails_closed(tmp_path):
    vault = tmp_path / "vault"
    path = _plant(vault, "registry_string_version.json", "registry.json")
    original = path.read_bytes()
    with pytest.raises(RegistryError, match="format_version is not an integer"):
        FileIdentityAccess(vault, MemoryLog()).find("agent")
    assert path.read_bytes() == original


def test_init_on_unversioned_registry_does_not_rewrite(tmp_path):
    vault = tmp_path / "vault"
    path = _plant(vault, "registry_unversioned.json", "registry.json")
    original = path.read_bytes()
    with pytest.raises(RegistryError, match="format_version is missing"):
        FileIdentityAccess(vault, MemoryLog()).init("other", "age1exampleother", "sops")
    assert path.read_bytes() == original


def test_json_secret_list_future_registry_does_not_wipe(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["init"], env).returncode == 0, "init failed"
    path = _plant(vault, "registry_future.json", "registry.json")
    original = path.read_bytes()
    proc = run_cli(["--json", "secret", "list"], env)
    assert proc.returncode == 1
    assert proc.stderr == ""
    data = json.loads(proc.stdout or proc.stderr)
    assert data["ok"] is False
    assert data["error"] == "error"
    assert data["reason"] == FUTURE_REGISTRY_MSG
    assert CANARY not in proc.stderr
    assert path.read_bytes() == original


def test_doctor_future_registry_is_not_uninitialized(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["init"], env).returncode == 0
    path = _plant(vault, "registry_future.json", "registry.json")
    original = path.read_bytes()
    proc = run_cli(["--json", "diagnose"], env)
    assert proc.returncode == 1
    assert proc.stderr == ""
    data = json.loads(proc.stdout or proc.stderr)
    assert data["ok"] is False
    assert data["reason"] == FUTURE_REGISTRY_MSG
    assert "initialized" not in data
    assert data["next"] != "agentself init"
    assert CANARY not in proc.stderr
    assert path.read_bytes() == original


@pytest.mark.parametrize("raw", [True, 1.0, None, 0])
def test_config_format_version_rejects_true_float_null_zero(tmp_path, raw):
    vault = tmp_path / "vault"
    vault.mkdir()
    path = vault / "config.json"
    path.write_text(
        json.dumps(
            {
                "identity_id": "agent",
                "age_key_file": "identities/agent/agent.agekey",
                "format_version": raw,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    original = path.read_bytes()
    with pytest.raises(IdentityStateError, match="format_version"):
        load_config(vault)
    assert path.read_bytes() == original

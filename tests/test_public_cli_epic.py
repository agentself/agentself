"""Epic #62: install skills/tools, diagnose ready, backup/restore, secret delete."""

from __future__ import annotations

import json
from pathlib import Path

from tests.support import cli_env, run_cli


def test_install_skills_project_and_global(tmp_path):
    env = cli_env(tmp_path / "vault")
    home = tmp_path / "home"
    home.mkdir()
    env["USERPROFILE"] = str(home)
    env["HOME"] = str(home)
    project = tmp_path / "proj"
    project.mkdir()
    proc = run_cli(["install", "--skills"], env, cwd=project)
    assert proc.returncode == 0, proc.stderr
    dest = project / ".claude" / "skills" / "agentself" / "SKILL.md"
    assert dest.is_file()
    gdest = home / ".claude" / "skills" / "agentself" / "SKILL.md"
    glob = run_cli(["install", "--skills", "-g"], env, cwd=project)
    assert glob.returncode == 0, glob.stderr
    assert gdest.is_file()
    agents = run_cli(["--json", "install", "--skills=agents"], env, cwd=project)
    assert agents.returncode == 0, agents.stderr
    data = json.loads(agents.stdout)
    assert data["ok"] is True
    assert data["paths"][0].endswith(
        str(Path(".agents") / "skills" / "agentself" / "SKILL.md")
    )
    grok = run_cli(["install", "--skills=grok"], env, cwd=project)
    assert grok.returncode == 2, grok.stdout + grok.stderr


def test_mailbox_flag_is_unknown(tmp_path):
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["init", "--mailbox", "agentmail"], env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "unrecognized arguments: --mailbox" in proc.stderr


def test_backup_restore_roundtrip(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    started = run_cli(["--json", "init"], env)
    assert started.returncode == 0, started.stderr
    addr = json.loads(started.stdout)["address"]
    dest = tmp_path / "copy"
    backup = run_cli(["--json", "backup", str(dest)], env)
    assert backup.returncode == 0, backup.stderr
    assert json.loads(backup.stdout)["ok"] is True
    refuse = run_cli(["backup", str(dest)], env)
    assert refuse.returncode == 1, refuse.stdout + refuse.stderr
    assert "not empty" in refuse.stderr
    forced = run_cli(["backup", str(dest), "--force"], env)
    assert forced.returncode == 0, forced.stderr
    restored = tmp_path / "restored"
    env2 = cli_env(restored)
    restore = run_cli(["--json", "restore", str(dest)], env2)
    assert restore.returncode == 0, restore.stderr
    shown = run_cli(["--json", "wallet", "address"], env2)
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["address"] == addr
    blob = restore.stdout + restore.stderr
    assert "AGE-SECRET-KEY" not in blob


def test_v1_config_fails_closed(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["init"], env).returncode == 0
    cfg_path = vault / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    original = cfg_path.read_bytes()
    v1 = {
        "format_version": 1,
        "principal_id": cfg["identity_id"],
        "age_key_file": cfg["age_key_file"],
        "wallet_binding": cfg["wallet_backend"],
        "mailbox_binding": cfg["email_backend"],
    }
    cfg_path.write_text(json.dumps(v1, indent=2) + "\n", encoding="utf-8")
    planted = cfg_path.read_bytes()
    shown = run_cli(["--json", "show"], env)
    assert shown.returncode == 1, shown.stdout + shown.stderr
    data = json.loads(shown.stdout or shown.stderr)
    assert data["ok"] is False
    assert "format_version 1 is unsupported" in data["reason"]
    assert cfg_path.read_bytes() == planted
    assert planted != original

"""Install skills and backup/restore of the identity directory."""

from __future__ import annotations

import json
from pathlib import Path

from tests.support import PROJECT_ROOT, cli_env, run_cli

SKILL = PROJECT_ROOT / "agentself" / "skills" / "agentself" / "SKILL.md"


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
    assert dest.read_text(encoding="utf-8") == SKILL.read_text(encoding="utf-8")
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


def test_install_blocked_path_is_one_line_error(tmp_path):
    env = cli_env(tmp_path / "vault")
    (tmp_path / ".agents").write_text("blocked", encoding="utf-8")
    proc = run_cli(["install", "--skills=agents"], env, cwd=tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr
    assert "Traceback" not in proc.stdout
    lines = [line for line in proc.stderr.splitlines() if line.strip()]
    assert len(lines) == 1, proc.stderr
    assert "error" in proc.stderr.lower()

    js = run_cli(["--json", "install", "--skills=agents"], env, cwd=tmp_path)
    assert js.returncode == 1, js.stdout + js.stderr
    data = json.loads(js.stdout)
    assert data["ok"] is False
    assert data["error"] == "error"
    assert data.get("reason")


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

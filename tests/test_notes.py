"""Encrypted notes persist across independent processes."""

from __future__ import annotations

import json
from pathlib import Path

from tests.support import cli_env, run_cli


def test_note_handoff_across_processes(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0
    created = json.loads(
        run_cli(["--json", "note", "create", "handoff", "keep this"], env).stdout
    )
    assert created == {"ok": True, "name": "handoff"}
    listed = json.loads(run_cli(["--json", "note", "list"], env).stdout)
    assert listed["names"] == ["handoff"]
    got = json.loads(run_cli(["--json", "note", "get", "handoff"], env).stdout)
    assert got["value"] == "keep this"
    dest = tmp_path / "note.txt"
    wrote = run_cli(["note", "get", "handoff", "--file", str(dest)], env)
    assert wrote.returncode == 0, wrote.stderr
    assert dest.read_text(encoding="utf-8") == "keep this"
    updated = json.loads(
        run_cli(["--json", "note", "update", "handoff", "next value"], env).stdout
    )
    assert updated == {"ok": True, "name": "handoff"}
    later = json.loads(run_cli(["--json", "note", "get", "handoff"], env).stdout)
    assert later["value"] == "next value"
    deleted = json.loads(run_cli(["--json", "note", "delete", "handoff"], env).stdout)
    assert deleted == {"ok": True, "name": "handoff"}
    missing = run_cli(["--json", "note", "get", "handoff"], env)
    assert missing.returncode == 3
    assert json.loads(missing.stdout)["error"] == "missing"
    secrets = json.loads(run_cli(["--json", "secret", "list"], env).stdout)
    assert all(not name.startswith("note.") for name in secrets["names"])

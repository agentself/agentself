from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentself.internal.custody.errors import MissingNote, Refused
from agentself.internal.files import identity_home

from tests.support import cli_env, init_identity, run_cli


def _cli_identity(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    proc = run_cli(["--json", "init"], env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return vault, env


def test_note_client_crud_upsert_modes_and_logs(app, monkeypatch):
    init_identity(app, monkeypatch)

    assert app.client.note_set("handoff", "next action") == "created"
    assert app.client.note_set("handoff", "next action") == "unchanged"
    assert app.client.note_set("handoff", "different") == "updated"
    assert app.client.note_get("handoff") == "different"
    assert app.client.note_list() == ["handoff"]
    assert app.client.note_exists("handoff") is True

    path = identity_home(app.vault, "P") / "notes" / "handoff"
    assert path.read_bytes() == b"different"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700

    app.client.note_delete("handoff")
    assert app.client.note_exists("handoff") is False
    with pytest.raises(MissingNote):
        app.client.note_get("handoff")

    rendered = app.log.rendered()
    assert "next action" not in rendered
    assert "different" not in rendered
    assert any(
        row["operation"] == "note_set" and row["name"] == "handoff"
        for row in app.log.records
    )


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a/b", ".", "..", "NUL"])
def test_note_names_refuse_path_traversal(app, monkeypatch, name):
    init_identity(app, monkeypatch)
    with pytest.raises(Refused):
        app.client.note_set(name, "value")
    assert not (app.vault.parent / "escape").exists()


def test_notes_are_isolated_by_identity(app, monkeypatch):
    init_identity(app, monkeypatch, "first", store="memory")
    app.client.note_set("handoff", "first value")
    init_identity(app, monkeypatch, "second", store="memory")
    assert app.client.note_list() == []
    app.client.note_set("handoff", "second value")

    app.bind(monkeypatch, "first")
    assert app.client.note_get("handoff") == "first value"
    app.bind(monkeypatch, "second")
    assert app.client.note_get("handoff") == "second value"


def test_note_cli_file_preserves_utf8_and_newlines(tmp_path):
    vault, env = _cli_identity(tmp_path)
    source = tmp_path / "handoff.txt"
    source.write_bytes(b"\xef\xbb\xbf" + "line café\r\nline two\n\n".encode())

    set_proc = run_cli(["--json", "note", "set", "handoff", "--file", str(source)], env)
    assert set_proc.returncode == 0, set_proc.stdout + set_proc.stderr
    assert json.loads(set_proc.stdout) == {
        "ok": True,
        "name": "handoff",
        "status": "created",
    }
    stored = identity_home(vault, "agent") / "notes" / "handoff"
    assert stored.read_bytes() == "line café\r\nline two\n\n".encode()

    got = run_cli(["--json", "note", "get", "handoff"], env)
    assert got.returncode == 0, got.stdout + got.stderr
    assert json.loads(got.stdout) == {
        "ok": True,
        "name": "handoff",
        "value": "line café\r\nline two\n\n",
    }


def test_note_cli_output_contract_and_source_refusal(tmp_path):
    _vault, env = _cli_identity(tmp_path)
    created = run_cli(["note", "set", "handoff", "public context"], env)
    assert (created.returncode, created.stdout, created.stderr) == (0, "created\n", "")
    got = run_cli(["note", "get", "handoff"], env)
    assert (got.returncode, got.stdout, got.stderr) == (0, "public context\n", "")
    listed = run_cli(["--json", "note", "list"], env)
    assert json.loads(listed.stdout) == {"ok": True, "names": ["handoff"]}
    exists = run_cli(["--json", "note", "exists", "handoff"], env)
    assert json.loads(exists.stdout) == {
        "ok": True,
        "name": "handoff",
        "exists": True,
    }

    source = tmp_path / "value.txt"
    source.write_text("file value", encoding="utf-8")
    clash = run_cli(
        ["--json", "note", "set", "other", "argv", "--file", str(source)], env
    )
    assert clash.returncode == 2
    assert json.loads(clash.stdout)["reason"] == "value and --file"

    deleted = run_cli(["--json", "note", "delete", "handoff"], env)
    assert json.loads(deleted.stdout) == {"ok": True, "name": "handoff"}
    missing = run_cli(["--json", "note", "exists", "handoff"], env)
    assert missing.returncode == 3
    assert json.loads(missing.stdout)["next"] == "agentself note list"


def test_notes_follow_backup_and_restore(tmp_path):
    _vault, env = _cli_identity(tmp_path / "source")
    set_proc = run_cli(["note", "set", "handoff", "resume from checkpoint"], env)
    assert set_proc.returncode == 0, set_proc.stdout + set_proc.stderr
    backup = tmp_path / "backup"
    copied = run_cli(["backup", str(backup)], env)
    assert copied.returncode == 0, copied.stdout + copied.stderr

    restored_vault = tmp_path / "restored"
    restored_env = cli_env(restored_vault)
    restored = run_cli(["restore", str(backup)], restored_env)
    assert restored.returncode == 0, restored.stdout + restored.stderr
    got = run_cli(["--json", "note", "get", "handoff"], restored_env)
    assert got.returncode == 0, got.stdout + got.stderr
    assert json.loads(got.stdout)["value"] == "resume from checkpoint"


def test_note_help_is_explicitly_non_secret(tmp_path):
    env = cli_env(tmp_path / "vault")
    top = run_cli(["--help"], env)
    assert "note" in top.stdout
    help_proc = run_cli(["note", "--help"], env)
    text = help_proc.stdout.lower()
    assert help_proc.returncode == 0
    assert "non-secret" in text
    for prohibited in (
        "credentials",
        "otps",
        "private keys",
        "secret values",
        "mail bodies",
    ):
        assert prohibited in text
    assert "encrypted" not in text

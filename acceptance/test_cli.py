from __future__ import annotations

import re
from pathlib import Path


def _write(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    return path


def test_identity_secret_and_cli_contract(cli) -> None:
    fresh = cli.json("show", expected_code=2)
    assert fresh.payload["ok"] is False
    assert fresh.payload["next"] == "agentself init"

    started = cli.json("init")
    repeated = cli.json("init")
    assert repeated.payload["address"] == started.payload["address"]
    assert repeated.payload["recipient"] == started.payload["recipient"]

    canary = "acceptance-secret-canary"
    source = _write(cli.root / "secret.txt", canary)
    created = cli.json("secret", "create", "demo.token", "--file", str(source))
    listed = cli.json("secret", "list")
    shown = cli.json("show")
    diagnosed = cli.json("diagnose")
    got = cli.json("secret", "get", "demo.token")
    assert got.payload["value"] == canary
    for result in (created, listed, shown, diagnosed, got):
        if result is got:
            continue
        assert canary not in result.output

    printed = cli.run("secret", "get", "demo.token", "--raw").expect(0)
    assert printed.stdout == canary
    assert printed.stderr == ""

    cli.json("secret", "delete", "demo.token")
    missing = cli.json("secret", "exists", "demo.token", expected_code=3)
    assert missing.payload["ok"] is False

    pass_identity = cli.root / "pass-identity"
    cli.json("init", "--store", "pass", identity_dir=pass_identity)
    cli.json("diagnose", identity_dir=pass_identity)


def test_backup_restore_preserves_identity(cli) -> None:
    started = cli.json("init")
    secret = "backup-secret-canary"
    note = "resume from the restored identity"
    secret_file = _write(cli.root / "backup-secret.txt", secret)
    note_file = _write(cli.root / "handoff.txt", note)
    cli.json("secret", "create", "demo.token", "--file", str(secret_file))
    cli.json("note", "set", "handoff", "--file", str(note_file))

    backup_dir = cli.root / "backup"
    backup = cli.json("backup", str(backup_dir))
    restored_dir = cli.root / "restored"
    restored = cli.json("restore", str(backup_dir), identity_dir=restored_dir)
    for result in (backup, restored):
        assert secret not in result.output
        assert "AGE-SECRET-KEY" not in result.output

    address = cli.json("wallet", "address", identity_dir=restored_dir)
    assert address.payload["address"] == started.payload["address"]
    restored_secret = cli.json("secret", "get", "demo.token", identity_dir=restored_dir)
    assert restored_secret.payload["value"] == secret
    restored_note = cli.json("note", "get", "handoff", identity_dir=restored_dir)
    assert restored_note.payload["value"] == note
    assert cli.json("diagnose", identity_dir=restored_dir).payload["ok"] is True


def test_email_discovery_is_read_only_and_actionable(cli) -> None:
    cli.json("init")
    before = cli.snapshot()

    menu = cli.json("email", "connect", expected_code=3)
    payload = menu.payload
    assert payload["status"] == "input_required"
    assert payload["human_action_required"] is True
    option = payload["option"]
    assert isinstance(option, dict)
    assert option["name"] == "setup_method"
    assert option["choices"] == ["existing_credential", "create_account"]
    assert str(payload["next"]).startswith(
        "agentself email connect --continue --state "
    )
    assert cli.snapshot() == before

    unknown = cli.json("email", "mark", "m9999", "acted", expected_code=2)
    assert unknown.payload["reason"] == "unknown mail ref"
    assert unknown.payload["next"] == "agentself email list"
    assert cli.snapshot() == before


def test_installed_skill_is_complete_and_truthful(cli) -> None:
    version = cli.json("--version")
    assert version.payload["cli"] == 2
    cli.run("--help").expect(0)

    project = cli.root / "empty-project"
    installed = cli.json("install", "--skills=agents", cwd=project)
    reported = {Path(str(path)).resolve() for path in installed.payload["paths"]}
    skill_dir = project / ".agents" / "skills" / "agentself"
    actual = {path.resolve() for path in skill_dir.rglob("*") if path.is_file()}
    assert reported == actual

    required = {
        Path("SKILL.md"),
        Path("references/email-connect.md"),
        Path("references/handoff.md"),
        Path("references/mail.md"),
    }
    relatives = {path.relative_to(skill_dir) for path in actual}
    assert required <= relatives
    for relative in required:
        assert (skill_dir / relative).read_bytes()

    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    links = re.findall(r"\[[^]]+\]\(([^)]+)\)", skill_text)
    local_links = [
        link for link in links if "://" not in link and not link.startswith("#")
    ]
    assert local_links
    for link in local_links:
        assert (skill_dir / link).is_file(), link

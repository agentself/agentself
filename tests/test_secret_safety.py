"""Protected wallet.key, reserved names, and idempotent secret create."""

from __future__ import annotations

import json
from pathlib import Path

from tests.support import cli_env, run_cli


def _init(tmp_path: Path) -> dict[str, str]:
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["--json", "init"], env)
    assert proc.returncode == 0, proc.stderr
    return env


def test_wallet_key_is_protected_and_requires_unsafe(tmp_path: Path) -> None:
    env = _init(tmp_path)
    listed = json.loads(run_cli(["--json", "secret", "list"], env).stdout)
    assert "wallet.key" in listed["names"]
    assert "wallet.key" in listed["protected"]
    refused = run_cli(["--json", "secret", "get", "wallet.key"], env)
    assert refused.returncode == 2
    payload = json.loads(refused.stdout)
    assert payload["error"] == "refused"
    assert "protected" in payload["reason"]
    exported = run_cli(["--json", "secret", "get", "wallet.key", "--unsafe"], env)
    assert exported.returncode == 0
    hexkey = json.loads(exported.stdout)["value"]
    assert hexkey.startswith("0x")
    dest = tmp_path / "wallet.key"
    wrote = run_cli(
        ["secret", "get", "wallet.key", "--unsafe", "--file", str(dest)], env
    )
    assert wrote.returncode == 0, wrote.stderr
    assert dest.read_text(encoding="utf-8").startswith("0x")


def test_secret_exists_and_meta(tmp_path: Path) -> None:
    env = _init(tmp_path)
    missing = run_cli(["--json", "secret", "exists", "demo.token"], env)
    assert missing.returncode == 3
    absent = json.loads(missing.stdout)
    assert absent["exists"] is False
    created = run_cli(["--json", "secret", "create", "demo.token", "alpha"], env)
    assert created.returncode == 0, created.stderr
    present = json.loads(
        run_cli(["--json", "secret", "exists", "demo.token"], env).stdout
    )
    assert present["ok"] is True
    assert present["exists"] is True
    meta = json.loads(
        run_cli(["--json", "secret", "get", "demo.token", "--meta"], env).stdout
    )
    assert "value" not in meta
    assert meta["bytes"] == 5
    assert len(meta["sha256"]) == 64


def test_same_value_create_is_unchanged(tmp_path: Path) -> None:
    env = _init(tmp_path)
    first = json.loads(
        run_cli(["--json", "secret", "create", "demo.token", "alpha"], env).stdout
    )
    assert first == {"ok": True, "name": "demo.token"}
    second = json.loads(
        run_cli(["--json", "secret", "create", "demo.token", "alpha"], env).stdout
    )
    assert second["unchanged"] is True
    clash = run_cli(["--json", "secret", "create", "demo.token", "beta"], env)
    assert clash.returncode == 2
    assert json.loads(clash.stdout)["error"] == "refused"


def test_reserved_secret_names_are_hidden(tmp_path: Path) -> None:
    env = _init(tmp_path)
    for name in ("internal.setup.demo", "note.demo"):
        proc = run_cli(["--json", "secret", "create", name, "x"], env)
        assert proc.returncode == 2
        assert json.loads(proc.stdout)["error"] == "refused"
    listed = json.loads(run_cli(["--json", "secret", "list"], env).stdout)
    assert all(not item.startswith("internal.") for item in listed["names"])
    assert all(not item.startswith("note.") for item in listed["names"])

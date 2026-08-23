"""Protected wallet.key, --meta without values, and reserved secret names."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.support import cli_env, run_cli, value_file


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
    if os.name != "nt":
        assert dest.stat().st_mode & 0o777 == 0o600
    new_key = "0x" + "ab" * 32
    updated = run_cli(
        [
            "--json",
            "secret",
            "update",
            "wallet.key",
            "--file",
            value_file(tmp_path, new_key, "new-key.txt"),
        ],
        env,
    )
    assert updated.returncode == 2, updated.stdout + updated.stderr
    payload = json.loads(updated.stdout)
    assert payload["error"] == "refused"
    assert "protected" in payload["reason"]
    still = json.loads(
        run_cli(["--json", "secret", "get", "wallet.key", "--unsafe"], env).stdout
    )["value"]
    assert still == hexkey
    forced = run_cli(
        [
            "--json",
            "secret",
            "update",
            "wallet.key",
            "--unsafe",
            "--file",
            value_file(tmp_path, new_key, "forced-key.txt"),
        ],
        env,
    )
    assert forced.returncode == 0, forced.stdout + forced.stderr
    replaced = json.loads(
        run_cli(["--json", "secret", "get", "wallet.key", "--unsafe"], env).stdout
    )["value"]
    assert replaced == new_key


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_secret_export_file_is_private_and_does_not_chmod_parent(
    tmp_path: Path,
) -> None:
    env = _init(tmp_path)
    parent = tmp_path / "export-dir"
    parent.mkdir(mode=0o755)
    dest = parent / "wallet.key"
    wrote = run_cli(
        ["secret", "get", "wallet.key", "--unsafe", "--file", str(dest)], env
    )
    assert wrote.returncode == 0, wrote.stderr
    assert dest.stat().st_mode & 0o777 == 0o600
    assert parent.stat().st_mode & 0o777 == 0o755


def test_secret_meta_omits_value(tmp_path: Path) -> None:
    env = _init(tmp_path)
    created = run_cli(
        [
            "--json",
            "secret",
            "create",
            "demo.token",
            "--file",
            value_file(tmp_path, "alpha"),
        ],
        env,
    )
    assert created.returncode == 0, created.stderr
    meta = json.loads(
        run_cli(["--json", "secret", "get", "demo.token", "--meta"], env).stdout
    )
    assert "value" not in meta
    assert meta["bytes"] == 5
    assert len(meta["sha256"]) == 64


def test_reserved_secret_names_are_hidden(tmp_path: Path) -> None:
    env = _init(tmp_path)
    proc = run_cli(
        [
            "--json",
            "secret",
            "create",
            "internal.setup.demo",
            "--file",
            value_file(tmp_path, "x"),
        ],
        env,
    )
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["error"] == "refused"
    listed = json.loads(run_cli(["--json", "secret", "list"], env).stdout)
    assert all(not item.startswith("internal.") for item in listed["names"])

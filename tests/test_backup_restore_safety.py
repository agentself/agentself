"""Backup/restore must not destroy the live identity or copy plaintext leftovers."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from agentself.cli.commands import identity as identity_cmd
from agentself.cli.commands.identity import _copy_identity_dir
from agentself.internal.files import LOCK_NAME, secrets_home
from agentself.local import IdentityStateError

from tests.support import cli_env, run_cli


def _init(tmp_path: Path) -> tuple[Path, dict[str, str], str]:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    proc = run_cli(["--json", "init"], env)
    assert proc.returncode == 0, proc.stderr
    addr = json.loads(proc.stdout)["address"]
    return vault, env, addr


def test_restore_force_from_empty_dir_keeps_identity(tmp_path: Path) -> None:
    vault, env, addr = _init(tmp_path)
    marker = vault / "config.json"
    before = marker.read_text(encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()
    proc = run_cli(["restore", "--force", str(empty)], env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8") == before
    shown = run_cli(["--json", "wallet", "address"], env)
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["address"] == addr


def test_restore_force_survives_copy_failure(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    (src / "config.json").write_text('{"format_version": 1}\n', encoding="utf-8")
    dest.mkdir()
    (dest / "config.json").write_text(
        '{"format_version": 1, "keep": "yes"}\n', encoding="utf-8"
    )
    (dest / "keep.txt").write_text("keep-me", encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copytree", boom)
    with pytest.raises(OSError, match="disk full"):
        _copy_identity_dir(src, dest, force=True)
    assert dest.is_dir()
    assert (dest / "keep.txt").read_text(encoding="utf-8") == "keep-me"
    assert '"keep": "yes"' in (dest / "config.json").read_text(encoding="utf-8")


def test_backup_omits_plaintext_secret_tmp(tmp_path: Path) -> None:
    vault, env, _addr = _init(tmp_path)
    secrets = secrets_home(vault, "agent")
    secrets.mkdir(parents=True, exist_ok=True)
    planted = secrets / "secret.leaked.tmp"
    planted.write_text("PLAINTEXT-SECRET-VALUE", encoding="utf-8")
    dest = tmp_path / "backup"
    proc = run_cli(["backup", str(dest)], env)
    assert proc.returncode == 0, proc.stderr
    leaked = list(dest.rglob("secret.leaked.tmp"))
    assert leaked == []
    assert planted.is_file()


def test_backup_force_refuses_parent_of_identity(tmp_path: Path) -> None:
    vault, env, addr = _init(tmp_path)
    parent = vault.parent
    proc = run_cli(["backup", "--force", str(parent)], env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert proc.stderr == ""
    assert "contains the identity directory" in json.loads(proc.stdout)["reason"]
    assert (vault / "config.json").is_file()
    shown = run_cli(["--json", "wallet", "address"], env)
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["address"] == addr


def test_copy_refuses_missing_config_without_touching_dest(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    (dest / "config.json").write_text("keep", encoding="utf-8")
    with pytest.raises(IdentityStateError, match="identity directory is missing"):
        _copy_identity_dir(src, dest, force=True)
    assert (dest / "config.json").read_text(encoding="utf-8") == "keep"


def test_copy_force_survives_dest_dir_rename_failure(
    tmp_path: Path, monkeypatch
) -> None:
    src, src_env, src_addr = _init(tmp_path / "src")
    dest, dest_env, dest_addr = _init(tmp_path / "dest")
    assert src_addr != dest_addr
    dest_resolved = dest.resolve()
    real_rename = os.rename
    real_replace = identity_cmd._replace_tree_contents
    replaced: list[bool] = []

    def rename(src_path, dst_path):
        try:
            same = Path(src_path).resolve() == dest_resolved
        except OSError:
            same = False
        if same:
            raise OSError("directory in use")
        return real_rename(src_path, dst_path)

    def replace_tree(staging: Path, dest_path: Path) -> None:
        replaced.append(True)
        real_replace(staging, dest_path)

    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(identity_cmd, "_replace_tree_contents", replace_tree)
    _copy_identity_dir(src, dest, force=True)
    assert replaced == [True]
    shown = run_cli(["--json", "wallet", "address"], dest_env)
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["address"] == src_addr
    src_shown = run_cli(["--json", "wallet", "address"], src_env)
    assert json.loads(src_shown.stdout)["address"] == src_addr
    assert (dest / "config.json").is_file()
    assert not dest.with_name(dest.name + ".agentself-prev").exists()
    assert not dest.with_name(dest.name + ".agentself-staging").exists()
    leftover_addr = run_cli(["--json", "wallet", "address"], dest_env)
    assert json.loads(leftover_addr.stdout)["address"] != dest_addr


def test_copy_does_not_require_lock_file_in_source(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    (src / "config.json").write_text('{"format_version": 1}\n', encoding="utf-8")
    (src / LOCK_NAME).write_text("\n", encoding="utf-8")
    _copy_identity_dir(src, dest, force=False)
    assert (dest / "config.json").is_file()
    assert not (dest / LOCK_NAME).exists()

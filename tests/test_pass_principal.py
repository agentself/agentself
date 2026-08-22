"""Pass principal setup is Python; a wheel must not need scripts/setup-principal.sh."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import agentself.local as local
from agentself.internal.custody.errors import HostToolMissing
from agentself.internal.files import identity_home
from agentself.local import ensure_age_key

from tests.support import PROJECT_ROOT, cli_env, run_cli


def test_pass_missing_tools_fails_closed_without_setup_script(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert not (PROJECT_ROOT / "scripts" / "setup-principal.sh").exists()

    def run_wrapped(argv, *args, **kwargs):
        cmd = argv if isinstance(argv, (list, tuple)) else [argv]
        assert not any("setup-principal.sh" in str(part) for part in cmd)
        raise AssertionError("host tools must not run when pass/gpg are missing")

    original_is_file = Path.is_file

    def is_file_wrapped(self):
        text = str(self).replace("\\", "/")
        assert not text.endswith("scripts/setup-principal.sh")
        return original_is_file(self)

    monkeypatch.setattr(subprocess, "run", run_wrapped)
    monkeypatch.setattr(Path, "is_file", is_file_wrapped)
    with pytest.raises(HostToolMissing, match="pass|gpg"):
        ensure_age_key(vault, "agent", store="pass")


def test_init_store_pass_missing_tools(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    empty = tmp_path / "empty"
    empty.mkdir()
    env["PATH"] = str(empty)
    proc = run_cli(["init", "--store", "pass"], env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    blob = proc.stdout + proc.stderr
    assert "Traceback" not in blob
    assert "AGE-SECRET-KEY" not in blob
    assert "setup-principal.sh" not in blob
    assert "pass" in proc.stderr
    assert "gpg" in proc.stderr
    lines = [line for line in proc.stderr.splitlines() if line.strip()]
    assert lines[0].startswith("error:")
    assert "pass" in lines[0] or "gpg" in lines[0]


def test_pass_setup_does_not_consult_missing_setup_script(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    store = identity_home(vault, "agent") / "password-store"
    store.mkdir(parents=True)
    (store / ".gpg-id").write_text("ok\n", encoding="utf-8")
    original_is_file = Path.is_file

    def is_file_wrapped(self):
        text = str(self).replace("\\", "/")
        if text.endswith("scripts/setup-principal.sh"):
            return False
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file_wrapped)
    monkeypatch.setattr(local, "_have_tool", lambda name: True)
    monkeypatch.setattr(local, "_gpg_has_secret", lambda *a, **k: True)
    monkeypatch.setattr(local, "_gpg_fingerprint", lambda *a, **k: "A" * 40)
    seen: list[list[str]] = []
    real_run = subprocess.run

    def run_wrapped(argv, *args, **kwargs):
        cmd = argv if isinstance(argv, (list, tuple)) else [argv]
        parts = [str(part) for part in cmd]
        seen.append(parts)
        assert not any("setup-principal.sh" in part for part in parts)
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run_wrapped)
    key = ensure_age_key(vault, "agent", store="pass")
    assert key.is_file()
    assert not any("setup-principal.sh" in " ".join(cmd) for cmd in seen)


def test_leftover_gpg_batch_is_shredded(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    pdir = identity_home(vault, "agent")
    pdir.mkdir(parents=True)
    leftover = pdir / ".gpg-batch"
    leftover.write_text("%no-protection\nName-Real: leftover\n", encoding="utf-8")
    (pdir / "agent.agekey").write_text(
        "AGE-SECRET-KEY-TESTLEFTOVER\n", encoding="utf-8"
    )
    store = pdir / "password-store"
    store.mkdir()
    (store / ".gpg-id").write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr(local, "_have_tool", lambda name: True)
    monkeypatch.setattr(local, "_gpg_has_secret", lambda *a, **k: True)
    monkeypatch.setattr(local, "_gpg_fingerprint", lambda *a, **k: "A" * 40)
    ensure_age_key(vault, "agent", store="pass")
    assert not leftover.exists()


@pytest.mark.skipif(
    shutil.which("gpg") is None or shutil.which("pass") is None,
    reason="pass store requires gpg and pass on PATH",
)
def test_pass_setup_creates_gnupg_and_password_store(tmp_path):
    vault = tmp_path / "vault"
    key = ensure_age_key(vault, "agent", store="pass")
    pdir = identity_home(vault, "agent")
    assert key.is_file()
    assert key == pdir / "agent.agekey"
    assert (pdir / "gnupg").is_dir()
    assert (pdir / "password-store" / ".gpg-id").is_file()
    assert not (pdir / ".gpg-batch").exists()
    blob = key.read_text(encoding="utf-8")
    assert "AGE-SECRET-KEY-" in blob
    gpg_id = (pdir / "password-store" / ".gpg-id").read_text(encoding="utf-8")
    assert "AGE-SECRET-KEY" not in gpg_id
    again = ensure_age_key(vault, "agent", store="pass")
    assert again == key
    assert not (pdir / ".gpg-batch").exists()


@pytest.mark.skipif(
    shutil.which("gpg") is None or shutil.which("pass") is None,
    reason="pass store requires gpg and pass on PATH",
)
def test_init_store_pass(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    proc = run_cli(["init", "--store", "pass"], env)
    assert proc.returncode == 0, proc.stderr
    assert "AGE-SECRET-KEY" not in proc.stdout + proc.stderr
    assert (identity_home(vault, "agent") / "password-store" / ".gpg-id").is_file()
    shown = run_cli(["--json", "diagnose"], env)
    assert shown.returncode == 0, shown.stdout + shown.stderr
    data = json.loads(shown.stdout)
    assert data["ok"] is True
    assert data["ready"]["email"] is False
    assert "AGE-SECRET-KEY" not in shown.stdout + shown.stderr

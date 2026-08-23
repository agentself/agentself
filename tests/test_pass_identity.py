"""Pass identity setup is Python; a wheel must not need scripts/setup-principal.sh."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

import agentself.local as local
from agentself.internal.custody.errors import HostToolMissing
from agentself.internal.files import identity_home
from agentself.internal.gpg import bindable_home
from agentself.local import ensure_age_key

from tests.support import PROJECT_ROOT, apply_cli_env, cli_env, run_cli


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
    assert data.get("reason") != "wallet.key is missing"
    assert "wallet.key is missing" not in shown.stdout + shown.stderr
    ready = data.get("ready")
    if isinstance(ready, dict) and "wallet" in ready:
        assert ready["wallet"] is True
    assert data["ready"]["email"] is False
    assert "AGE-SECRET-KEY" not in shown.stdout + shown.stderr


def test_gpg_keygen_error_includes_redacted_stderr(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    pdir = identity_home(vault, "agent")
    pdir.mkdir(parents=True)
    (pdir / "agent.agekey").write_text(
        "AGE-SECRET-KEY-TESTKEYGENERR\n", encoding="utf-8"
    )
    monkeypatch.setattr(local, "_have_tool", lambda name: True)

    def fake_run(argv, *, env=None, timeout=30, failed=""):
        if "--generate-key" in argv:
            return subprocess.CompletedProcess(
                argv,
                2,
                b"",
                b"gpg: generating identity GPG key\n"
                b"gpg: AGE-SECRET-KEY-LEAKME\n"
                b"gpg-agent[1]: socket name is too long\n",
            )
        if "--list-secret-keys" in argv:
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(local, "_run_host", fake_run)
    with pytest.raises(RuntimeError, match="socket name is too long") as caught:
        ensure_age_key(vault, "agent", store="pass")
    msg = str(caught.value)
    assert "gpg keygen failed" in msg
    assert "AGE-SECRET-KEY-LEAKME" not in msg


def test_host_failure_message_redacts_secret_values():
    proc = subprocess.CompletedProcess(
        ["gpg"],
        2,
        b"",
        b"gpg: cannot use AGE-SECRET-KEY-LEAKME\n",
    )
    msg = local._host_failure_message("gpg keygen failed", proc)
    assert "AGE-SECRET-KEY-LEAKME" not in msg
    assert "AGE-SECRET-KEY-[redacted]" in msg
    assert msg.startswith("gpg keygen failed:")


@pytest.mark.skipif(os.name == "nt", reason="unix socket path limit")
def test_bindable_home_uses_short_symlink(tmp_path):
    gnupg = tmp_path / ("n" * 80) / "identities" / "agent" / "gnupg"
    gnupg.mkdir(parents=True, mode=0o700)
    home = bindable_home(gnupg)
    assert home != gnupg
    assert home.is_symlink()
    assert home.resolve() == gnupg.resolve()
    assert len(os.fsencode(home / "S.gpg-agent.browser")) <= 106
    assert str(home).startswith("/tmp/as-gpg-")
    assert bindable_home(gnupg) == home


@pytest.mark.skipif(os.name != "nt", reason="Windows TEMP GNUPGHOME link")
def test_bindable_home_uses_short_temp_link(tmp_path):
    gnupg = tmp_path / ("n" * 80) / "identities" / "agent" / "gnupg"
    gnupg.mkdir(parents=True)
    probe = tmp_path / "probe-link"
    try:
        probe.symlink_to(gnupg, target_is_directory=True)
    except OSError as symlink_exc:
        try:
            import _winapi

            _winapi.CreateJunction(str(gnupg), str(probe))
        except OSError as exc:
            pytest.skip(f"symlinks not available: {exc}")
        except Exception:
            pytest.skip(f"symlinks not available: {symlink_exc}")
    try:
        probe.unlink()
    except OSError:
        pass
    home = bindable_home(gnupg)
    assert home != gnupg
    assert home.parent.resolve() == Path(tempfile.gettempdir()).resolve()
    assert home.name.startswith("as-gpg-")
    try:
        home.readlink()
    except OSError:
        pytest.fail("expected a symlink or junction")
    assert home.resolve() == gnupg.resolve()
    assert bindable_home(gnupg) == home
    assert len(os.fsencode(home / "S.gpg-agent.browser")) < len(
        os.fsencode(gnupg / "S.gpg-agent.browser")
    )


def test_bindable_home_returns_gnupg_when_link_fails(tmp_path, monkeypatch):
    gnupg = tmp_path / "identities" / "agent" / "gnupg"
    gnupg.mkdir(parents=True)

    def fail_symlink(self, target, target_is_directory=False):
        raise OSError("symlink denied")

    monkeypatch.setattr(Path, "symlink_to", fail_symlink)
    if os.name == "nt":
        import _winapi

        def fail_junction(src, dest):
            raise OSError("junction denied")

        monkeypatch.setattr(_winapi, "CreateJunction", fail_junction)
    assert bindable_home(gnupg) == gnupg


@pytest.mark.skipif(
    shutil.which("gpg") is None or shutil.which("pass") is None,
    reason="pass store requires gpg and pass on PATH",
)
def test_pass_setup_survives_long_identity_dir_path(tmp_path):
    vault = tmp_path / ("n" * 80) / "vault"
    extra = vault / "identities" / "agent" / "gnupg" / "S.gpg-agent.browser"
    assert len(os.fsencode(extra)) > 107
    key = ensure_age_key(vault, "agent", store="pass")
    pdir = identity_home(vault, "agent")
    assert key.is_file()
    assert (pdir / "gnupg").is_dir()
    assert (pdir / "password-store" / ".gpg-id").is_file()
    assert not (pdir / ".gpg-batch").exists()


@pytest.mark.skipif(
    shutil.which("gpg") is None or shutil.which("pass") is None,
    reason="pass store requires gpg and pass on PATH",
)
def test_init_surfaces_gpg_keygen_detail(tmp_path, monkeypatch, capsys):
    from agentself.cli.app import main

    vault = tmp_path / "vault"
    env = cli_env(vault)
    apply_cli_env(monkeypatch, env)
    monkeypatch.setattr(
        "agentself.cli.app.ensure_age_key",
        lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("gpg keygen failed: socket name is too long")
        ),
    )
    code = main(["init", "--store", "pass"])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "Traceback" not in captured.err
    assert captured.err.startswith(
        "error: gpg keygen failed: socket name is too long\n"
    )
    js = main(["--json", "init", "--store", "pass"])
    blob = capsys.readouterr()
    assert js == 1
    assert blob.err == ""
    data = json.loads(blob.out)
    assert data["ok"] is False
    assert data["error"] == "error"
    assert "socket name is too long" in data["reason"]

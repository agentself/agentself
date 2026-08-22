"""sops encrypt uses a real tempfile, not /dev/stdin."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentself.backends.store.contract import StoreResourceError
from agentself.backends.store.sops import SopsStoreAccess
from agentself.internal.files import identity_home, secrets_home
from agentself.internal.log import MemoryLog

from tests.support import PROJECT_ROOT

SOPS_STORE = PROJECT_ROOT / "agentself" / "backends" / "store" / "sops" / "__init__.py"
FAKE_RECIPIENT = "age1testrecipientnotarealkey"
CIPHERTEXT = b"sops-dummy-ciphertext"


def test_sops_store_source_has_no_dev_stdin():
    source = SOPS_STORE.read_text(encoding="utf-8")
    assert "/dev/stdin" not in source


def test_seal_encrypts_from_tempfile_then_unlinks(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    principal_id = "P"
    key = identity_home(vault, principal_id) / "agent.agekey"
    key.parent.mkdir(parents=True)
    key.write_bytes(b"dummy-age-key\n")
    secret = "plain-secret-value-must-not-remain"
    seen_tmp: list[Path] = []

    def fake_run_cmd(argv, *, env=None, stdin=None, timeout=30):
        cmd = list(argv)
        if cmd[0] == "age-keygen":
            return subprocess.CompletedProcess(
                cmd, 0, stdout=(FAKE_RECIPIENT + "\n").encode(), stderr=b""
            )
        if cmd[0] == "sops":
            assert "/dev/stdin" not in cmd
            assert "-i" not in cmd
            assert stdin is None
            input_path = Path(cmd[-1])
            assert input_path.is_file()
            assert input_path.suffix != ".sops"
            assert input_path.read_bytes() == secret.encode("utf-8")
            seen_tmp.append(input_path)
            return subprocess.CompletedProcess(cmd, 0, stdout=CIPHERTEXT, stderr=b"")
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr(
        "agentself.backends.store.sops.run_cmd",
        fake_run_cmd,
    )
    log = MemoryLog()
    store = SopsStoreAccess(vault, log)
    store.seal(principal_id, "token", secret)

    sealed = secrets_home(vault, principal_id) / "token.sops"
    assert sealed.is_file()
    assert sealed.read_bytes() == CIPHERTEXT
    assert seen_tmp
    for path in seen_tmp:
        assert not path.exists()
    leftovers = [
        path
        for path in sealed.parent.iterdir()
        if path.is_file() and path.suffix != ".sops"
    ]
    assert leftovers == []
    sink = log.rendered()
    assert secret not in sink
    assert "AGE-SECRET-KEY" not in sink


def test_seal_failed_does_not_leak_secret_or_leave_plaintext(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    principal_id = "P"
    key = identity_home(vault, principal_id) / "agent.agekey"
    key.parent.mkdir(parents=True)
    key.write_bytes(b"dummy-age-key\n")
    secret = "plain-secret-must-not-appear-in-error"
    key_path = str(key)
    seen_tmp: list[Path] = []

    def fake_run_cmd(argv, *, env=None, stdin=None, timeout=30):
        cmd = list(argv)
        if cmd[0] == "age-keygen":
            return subprocess.CompletedProcess(
                cmd, 0, stdout=(FAKE_RECIPIENT + "\n").encode(), stderr=b""
            )
        if cmd[0] == "sops":
            assert "/dev/stdin" not in cmd
            input_path = Path(cmd[-1])
            assert input_path.is_file()
            seen_tmp.append(input_path)
            return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"")
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr(
        "agentself.backends.store.sops.run_cmd",
        fake_run_cmd,
    )
    store = SopsStoreAccess(vault, MemoryLog())
    with pytest.raises(StoreResourceError, match="^seal failed$") as excinfo:
        store.seal(principal_id, "token", secret)
    message = str(excinfo.value)
    assert secret not in message
    assert FAKE_RECIPIENT not in message
    assert key_path not in message
    assert "AGE-SECRET-KEY" not in message
    assert seen_tmp
    for path in seen_tmp:
        assert not path.exists()
    hold = secrets_home(vault, principal_id)
    assert list(hold.iterdir()) == []

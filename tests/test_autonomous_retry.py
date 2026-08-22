"""Retries, crashes, and concurrent processes against a local vault.

Autonomous agents re-run commands, die mid-write, and overlap processes.
Safe verbs must be idempotent. wallet send must not pay twice.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from agentself.backends.store.contract import StoreResourceError
from agentself.backends.store.sops import SopsStoreAccess
from agentself.backends.wallet.contract import WalletError
from agentself.internal.custody.errors import ChannelFailure, Refused
from agentself.internal.files import (
    VaultBusy,
    atomic_write,
    exclusive,
    identity_home,
    secrets_home,
)
from agentself.internal.log import MemoryLog
from agentself.local import VaultStateError, load_config

from tests.support import (
    MockRpc,
    build_app,
    cli_env,
    enroll_principal,
    plant_email,
    run_cli,
)

_TO = "0x" + "11" * 20


class FailSendOnce(MockRpc):
    """First eth_sendRawTransaction raises. Does not record the tx as known."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fail_send = True

    def request(self, method: str, params: list[object]) -> object:
        if method == "eth_sendRawTransaction" and self._fail_send:
            self._fail_send = False
            self.calls.append((method, ["0x"]))
            raise WalletError("rpc failed")
        return super().request(method, params)


class FailAfterAccept(MockRpc):
    """Node accepted the tx; the HTTP response still fails."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fail_send = True

    def request(self, method: str, params: list[object]) -> object:
        if method == "eth_sendRawTransaction" and self._fail_send:
            self._fail_send = False
            self.calls.append((method, ["0x"]))
            self.broadcast = True
            raw = str(params[0] if params else "")
            self.sent_raw.append(raw)
            raise WalletError("rpc failed")
        return super().request(method, params)


def test_atomic_write_crash_keeps_previous_bytes(tmp_path, monkeypatch):
    dest = tmp_path / "hold.sops"
    dest.write_bytes(b"previous-ciphertext")
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        atomic_write(dest, b"new-ciphertext")
    assert dest.read_bytes() == b"previous-ciphertext"
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_exclusive_is_reentrant_and_serializes_threads(tmp_path):
    with exclusive(tmp_path):
        with exclusive(tmp_path):
            assert (tmp_path / "vault.lock").is_file()
    seen: list[int] = []

    def worker(tag: int) -> None:
        with exclusive(tmp_path):
            seen.append(tag)
            time.sleep(0.05)
            seen.append(tag)

    threads = [
        threading.Thread(target=worker, args=(1,)),
        threading.Thread(target=worker, args=(2,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert seen in ([1, 1, 2, 2], [2, 2, 1, 1])


def test_exclusive_timeout_is_vault_busy(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with exclusive(tmp_path):
            started.set()
            release.wait(2)

    thread = threading.Thread(target=holder)
    thread.start()
    assert started.wait(1)
    with pytest.raises(VaultBusy):
        with exclusive(tmp_path, timeout=0.2):
            pass
    release.set()
    thread.join()


def test_duplicate_init_keeps_the_same_wallet(tmp_path):
    env = cli_env(tmp_path / "vault")
    first = run_cli(["--json", "init"], env)
    second = run_cli(["--json", "init"], env)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    a = json.loads(first.stdout)
    b = json.loads(second.stdout)
    assert a["address"] == b["address"]
    assert a["recipient"] == b["recipient"]
    key_files = list((tmp_path / "vault").rglob("agent.agekey"))
    assert len(key_files) == 1


def test_concurrent_init_one_identity(tmp_path):
    env = cli_env(tmp_path / "vault")
    results: list = []

    def worker() -> None:
        results.append(run_cli(["--json", "init"], env))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [proc.returncode for proc in results] == [0, 0]
    payloads = [json.loads(proc.stdout) for proc in results]
    assert payloads[0]["address"] == payloads[1]["address"]
    assert payloads[0]["recipient"] == payloads[1]["recipient"]
    keys = list((tmp_path / "vault").rglob("agent.agekey"))
    assert len(keys) == 1


def test_secret_create_same_value_is_idempotent(app, monkeypatch):
    enroll_principal(app, monkeypatch)
    app.gateway.seal("token", "same")
    app.gateway.seal("token", "same")
    assert app.gateway.reveal("token") == "same"


def test_secret_create_different_value_still_refuses(app, monkeypatch):
    enroll_principal(app, monkeypatch)
    app.gateway.seal("token", "first")
    with pytest.raises(Refused):
        app.gateway.seal("token", "other")
    assert app.gateway.reveal("token") == "first"


def test_cli_secret_create_retry_same_value(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["init"], env).returncode == 0
    first = run_cli(["--json", "secret", "create", "notes", "alpha"], env)
    second = run_cli(["--json", "secret", "create", "notes", "alpha"], env)
    clash = run_cli(["--json", "secret", "create", "notes", "beta"], env)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert clash.returncode == 2, clash.stderr
    got = run_cli(["secret", "get", "notes"], env)
    assert got.returncode == 0
    assert got.stdout == "alpha\n"


def test_empty_sops_file_is_interrupted_write(app, monkeypatch):
    enroll_principal(app, monkeypatch)
    hold = secrets_home(app.vault, "P")
    hold.mkdir(parents=True, exist_ok=True)
    leftover = hold / "notes.sops"
    leftover.write_bytes(b"")
    app.gateway.seal("notes", "recovered")
    assert app.gateway.reveal("notes") == "recovered"
    assert leftover.stat().st_size > 0


def test_seal_write_failure_does_not_leave_partial_ciphertext(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    principal_id = "P"
    key = identity_home(vault, principal_id) / "agent.agekey"
    key.parent.mkdir(parents=True)
    key.write_bytes(b"dummy-age-key\n")
    ciphertext = b"complete-sops-bytes"

    def fake_run_cmd(argv, *, env=None, stdin=None, timeout=30):
        import subprocess

        cmd = list(argv)
        if cmd[0] == "age-keygen":
            return subprocess.CompletedProcess(
                cmd, 0, stdout=b"age1testrecipientnotarealkey\n", stderr=b""
            )
        if cmd[0] == "sops":
            return subprocess.CompletedProcess(cmd, 0, stdout=ciphertext, stderr=b"")
        raise AssertionError(cmd)

    monkeypatch.setattr("agentself.backends.store.sops.run_cmd", fake_run_cmd)
    monkeypatch.setattr(
        "agentself.internal.files.os.replace",
        lambda src, dst: (_ for _ in ()).throw(OSError("crash")),
    )
    store = SopsStoreAccess(vault, MemoryLog())
    with pytest.raises(StoreResourceError, match="^seal failed$"):
        store.seal(principal_id, "token", "plain-secret")
    dest = secrets_home(vault, principal_id) / "token.sops"
    assert not dest.exists()


def test_corrupt_config_fails_closed(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["init"], env).returncode == 0
    (vault / "config.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises(VaultStateError, match="cannot read config.json"):
        load_config(vault)
    show = run_cli(["show"], env)
    assert show.returncode == 1, show.stderr
    assert "cannot read config.json" in show.stderr
    assert "Traceback" not in show.stderr
    started = run_cli(["init"], env)
    assert started.returncode == 1, started.stdout + started.stderr
    assert "cannot read config.json" in started.stderr
    doctor = run_cli(["diagnose"], env)
    assert doctor.returncode == 1
    assert "cannot read config.json" in doctor.stderr


def test_corrupt_registry_fails_closed(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    assert run_cli(["init"], env).returncode == 0
    (vault / "registry.json").write_text("{not-json", encoding="utf-8")
    listed = run_cli(["secret", "list"], env)
    assert listed.returncode == 1, listed.stderr
    assert "cannot read registry.json" in listed.stderr
    assert "Traceback" not in listed.stderr
    js = run_cli(["--json", "secret", "list"], env)
    data = json.loads(js.stderr)
    assert data["ok"] is False
    assert data["reason"] == "cannot read registry.json"


def test_concurrent_secret_create_same_name(app, monkeypatch):
    enroll_principal(app, monkeypatch)
    errors: list[str] = []

    def writer(value: str) -> None:
        try:
            app.gateway.seal("race", value)
        except Refused:
            errors.append(value)

    threads = [
        threading.Thread(target=writer, args=("one",)),
        threading.Thread(target=writer, args=("two",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    held = app.gateway.reveal("race")
    assert held in ("one", "two")
    assert len(errors) == 1
    assert errors[0] != held


def test_email_recv_second_call_empty(app, monkeypatch):
    enroll_principal(app, monkeypatch)
    plant_email(app.vault, "P", from_addr="a@example.com", subject="once", body="body")
    first = app.gateway.email_recv()
    assert len(first) == 1
    assert first[0]["subject"] == "once"
    assert app.gateway.email_recv() == []
    listed = app.gateway.email_list()
    assert any(item["subject"] == "once" for item in listed)
    again = app.gateway.email_recv(message_id=first[0]["id"])
    assert len(again) == 1
    assert again[0]["subject"] == "once"


def test_email_recv_crash_before_move_redelivers(app, monkeypatch):
    enroll_principal(app, monkeypatch)
    planted = plant_email(
        app.vault, "P", from_addr="a@example.com", subject="hanging", body="x"
    )
    original = Path.replace
    crashed = {"n": 0}

    def boom(self: Path, target) -> Path:
        if self.parent.name == "new" and crashed["n"] == 0:
            crashed["n"] += 1
            raise OSError("crash")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError):
        app.gateway.email_recv()
    assert planted.is_file()
    monkeypatch.setattr(Path, "replace", original)
    got = app.gateway.email_recv()
    assert len(got) == 1
    assert got[0]["subject"] == "hanging"
    assert app.gateway.email_recv() == []


def test_concurrent_email_recv_each_message_once(app, monkeypatch):
    enroll_principal(app, monkeypatch)
    for i in range(4):
        plant_email(
            app.vault,
            "P",
            from_addr="a@example.com",
            subject=f"m{i}",
            body="b",
        )
    bags: list[list[dict[str, str]]] = []

    def worker() -> None:
        bags.append(app.gateway.email_recv())

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    ids = [msg["id"] for bag in bags for msg in bag]
    assert len(ids) == 4
    assert len(set(ids)) == 4
    assert app.gateway.email_recv() == []


def test_wallet_send_retry_after_timeout_resubmits_same_raw(vault, monkeypatch):
    rpc = FailSendOnce(eth_wei=10**18, usdc_raw=2_000_000)
    app = build_app(vault, rpc=rpc)
    enroll_principal(app, monkeypatch)
    with pytest.raises(ChannelFailure) as caught:
        app.gateway.wallet_send(_TO, "1")
    assert caught.value.reason == "rpc"
    assert rpc.broadcast is False
    first_sends = [c for c in rpc.calls if c[0] == "eth_sendRawTransaction"]
    assert len(first_sends) == 1
    pending = identity_home(vault, "P") / "wallet" / "pending-send.json"
    assert pending.is_file()
    record = json.loads(pending.read_text(encoding="utf-8"))
    raw = record["raw"]
    assert raw.startswith("0x")
    assert "AGE-SECRET-KEY" not in pending.read_text(encoding="utf-8")
    app.gateway.wallet_send(_TO, "1")
    sends = [c for c in rpc.calls if c[0] == "eth_sendRawTransaction"]
    assert len(sends) == 2
    assert rpc.sent_raw == [raw]
    assert rpc.broadcast is True


def test_wallet_send_timeout_after_accept_is_success_not_a_second_tx(
    vault, monkeypatch
):
    rpc = FailAfterAccept(eth_wei=10**18, usdc_raw=2_000_000)
    app = build_app(vault, rpc=rpc)
    enroll_principal(app, monkeypatch)
    app.gateway.wallet_send(_TO, "1")
    first_sends = [c for c in rpc.calls if c[0] == "eth_sendRawTransaction"]
    assert len(first_sends) == 1
    app.gateway.wallet_send(_TO, "1")
    sends = [c for c in rpc.calls if c[0] == "eth_sendRawTransaction"]
    assert len(sends) == 1
    key = app.gateway.reveal("wallet.key")
    sink = app.log.rendered()
    assert key not in sink
    assert key.lower().removeprefix("0x") not in sink.lower()


def test_wallet_send_different_destination_is_a_new_payment(vault, monkeypatch):
    rpc = MockRpc(eth_wei=10**18, usdc_raw=5_000_000)
    app = build_app(vault, rpc=rpc)
    enroll_principal(app, monkeypatch)
    app.gateway.wallet_send(_TO, "1")
    other = "0x" + "22" * 20
    app.gateway.wallet_send(other, "1")
    sends = [c for c in rpc.calls if c[0] == "eth_sendRawTransaction"]
    assert len(sends) == 2
    assert len(rpc.sent_raw) == 2
    assert rpc.sent_raw[0] != rpc.sent_raw[1]


def test_wallet_send_rpc_failure_before_broadcast_has_no_pending_ack(
    vault, monkeypatch
):
    class BoomRpc:
        def request(self, method: str, params: list[object]) -> object:
            raise WalletError("rpc failed")

    app = build_app(vault, rpc=BoomRpc())
    enroll_principal(app, monkeypatch)
    with pytest.raises(ChannelFailure) as caught:
        app.gateway.wallet_send(_TO, "1")
    assert caught.value.reason == "rpc"
    pending = identity_home(vault, "P") / "wallet" / "pending-send.json"
    assert not pending.is_file()


def test_imap_recv_marks_after_fetch_and_survives_mark_failure(vault):
    from tests.test_imap_mailbox import (
        ADDRESS,
        CANARY,
        PRINCIPAL,
        FakeImap,
        FakeSmtp,
        _box,
        _raw,
    )

    log = MemoryLog()
    imap = FakeImap(
        messages=[
            {"uid": "1", "raw": _raw(uid="1", subject="a"), "seen": False},
            {"uid": "2", "raw": _raw(uid="2", subject="b"), "seen": False},
        ]
    )

    def boom_mark(uid: str) -> None:
        raise OSError("disconnected")

    imap.mark_seen = boom_mark  # type: ignore[method-assign]
    smtp = FakeSmtp()
    mb = _box(vault, log, imap, smtp)
    got = mb.recv(PRINCIPAL, send_token=CANARY, address=ADDRESS)
    assert [m["subject"] for m in got] == ["a", "b"]
    assert CANARY not in log.rendered()

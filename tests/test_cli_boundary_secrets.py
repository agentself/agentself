"""CLI boundary: secrets never escape errors, logs, views, files, or subprocesses."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentself.backends.email.agentmail import _safe_filename
from agentself.backends.email.contract import MailboxError, require_secret
from agentself.backends.store.contract import StoreResourceError
from agentself.backends.store.run import run_cmd
from agentself.backends.store.sops import SopsStoreAccess
from agentself.backends.wallet.chain import _hex_int, _ok_hash, _send_result
from agentself.backends.wallet.contract import (
    CannotSend as WalletCannotSend,
)
from agentself.backends.wallet.contract import (
    WalletError,
)
from agentself.cli.app import main
from agentself.internal.custody.errors import CannotSend, StoreFailure
from agentself.internal.files import (
    identity_home,
    resolve_tool,
    secrets_home,
    shred_unlink,
)
from agentself.internal.log import MemoryLog
from agentself.internal.registry import FileIdentityAccess, RegistryError
from agentself.local import IdentityStateError, ensure_age_key, resolve_age_key_file

from tests.support import (
    apply_cli_env,
    build_app,
    cli_env,
    init_identity,
    run_cli,
    symlink_or_skip,
    value_file,
)

AGE_CANARY = "AGE-SECRET-KEY-1CANARYBOUNDARYTEST"
TOKEN_CANARY = "hold-token-CANARY-boundary"
WALLET_CANARY = "0x" + "ab" * 32
PLAIN_CANARY = "plain-secret-CANARY-must-not-escape"


class _LeakMailbox:
    def send(self, identity_id, to, subject, body, credential=None, address=None):
        raise MailboxError(f"send failed token={credential}")

    def receive(
        self,
        identity_id,
        *,
        credential=None,
        address=None,
        message_id=None,
        include_body=True,
    ):
        del include_body
        return [
            {
                "id": "1",
                "from": "a@b.c",
                "to": "c@d.e",
                "subject": "hi",
                "body": "ok",
                "credential": credential or TOKEN_CANARY,
                "wallet.key": WALLET_CANARY,
            }
        ]

    def list(self, identity_id, *, credential=None, address=None):
        return self.receive(identity_id, credential=credential, address=address)

    def describe(self, identity_id, *, credential=None, address=None):
        return {
            "address": "inbox@example.com",
            "owned_address": True,
            "needs_domain": False,
            "credential": credential or TOKEN_CANARY,
            "private_key": AGE_CANARY,
        }

    def setup_options(self):
        return ()

    def connect(
        self, identity_id, *, credential=None, address=None, answers=None, state=None
    ):
        del answers, state
        return self.describe(identity_id, credential=credential, address=address)


class _LeakMailboxFactory:
    def for_binding(self, binding: str):
        return _LeakMailbox()


class _LeakWallet:
    def required_material(self):
        return None

    def address(self, identity_id):
        return "0x" + "11" * 20

    def authorize(self, identity_id, message):
        raise WalletCannotSend(f"need USDC; key={WALLET_CANARY}")

    def balance(self, identity_id):
        return {
            "asset": "USDC",
            "amount": "0",
            "address": "0x" + "11" * 20,
            "private_key": WALLET_CANARY,
            "credential": TOKEN_CANARY,
        }

    def send(self, identity_id, to, amount, asset):
        raise WalletCannotSend(f"need USDC; key={WALLET_CANARY}")

    def describe(self, identity_id):
        return {
            "address": "0x" + "11" * 20,
            "asset": "USDC",
            "chain": "base",
            "private_key": WALLET_CANARY,
            "credential": TOKEN_CANARY,
        }


class _LeakWalletFactory:
    def for_binding(self, binding: str):
        return _LeakWallet()


def test_flag_tokens_after_doubledash_are_stored_as_values(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    for name, value in (
        ("jsonflag", "--json"),
        ("versionflag", "--version"),
    ):
        created = run_cli(
            [
                "secret",
                "create",
                name,
                "--file",
                value_file(tmp_path, value, name + ".txt"),
            ],
            env,
        )
        assert created.returncode == 0, created.stderr
        assert value not in created.stdout
        got = run_cli(["secret", "get", name, "--print"], env)
        assert got.returncode == 0, got.stderr
        assert got.stdout.strip() == value
        listed = run_cli(["--json", "secret", "list"], env)
        assert listed.returncode == 0, listed.stderr
        assert value not in listed.stdout + listed.stderr


def test_argparse_json_does_not_echo_age_secret(tmp_path):
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["--json", AGE_CANARY], env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    blob = proc.stdout + proc.stderr
    assert AGE_CANARY not in blob
    assert "Traceback" not in blob
    data = json.loads(proc.stdout or proc.stderr)
    assert data["ok"] is False
    assert AGE_CANARY not in json.dumps(data)


def test_secret_create_value_stays_off_stderr_logs_and_list(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    env["AGENTSELF_LOG"] = "1"
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    assert AGE_CANARY not in start.stdout + start.stderr
    assert WALLET_CANARY not in start.stdout + start.stderr

    created = run_cli(
        [
            "--json",
            "secret",
            "create",
            "notes",
            "--file",
            value_file(tmp_path, PLAIN_CANARY),
        ],
        env,
    )
    assert created.returncode == 0, created.stderr
    blob = created.stdout + created.stderr
    assert PLAIN_CANARY not in blob
    payload = json.loads(created.stdout)
    assert payload == {"ok": True, "name": "notes"}

    listed = run_cli(["--json", "secret", "list"], env)
    assert listed.returncode == 0, listed.stderr
    assert PLAIN_CANARY not in listed.stdout + listed.stderr
    assert json.loads(listed.stdout)["names"] == ["notes", "wallet.key"]

    shown = run_cli(["--json", "show"], env)
    assert shown.returncode == 0, shown.stderr
    assert PLAIN_CANARY not in shown.stdout + shown.stderr
    assert AGE_CANARY not in shown.stdout + shown.stderr

    doctor = run_cli(["--json", "diagnose"], env)
    assert doctor.returncode == 0, doctor.stderr
    assert PLAIN_CANARY not in doctor.stdout + doctor.stderr

    got = run_cli(["secret", "get", "notes", "--print"], env)
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == PLAIN_CANARY


def test_malformed_registry_recipient_fails_closed(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    registry = vault / "registry.json"
    data = json.loads(registry.read_text(encoding="utf-8"))
    records = data["identities"]
    records["agent"]["recipient"] = AGE_CANARY
    registry.write_text(json.dumps(data) + "\n", encoding="utf-8")

    shown = run_cli(["show"], env)
    blob = shown.stdout + shown.stderr
    assert shown.returncode != 0
    assert AGE_CANARY not in blob
    assert "Traceback" not in blob

    ident = run_cli(["--json", "show"], env)
    blob = ident.stdout + ident.stderr
    assert ident.returncode != 0
    assert AGE_CANARY not in blob
    assert "Traceback" not in blob

    doctor = run_cli(["--json", "diagnose"], env)
    blob = doctor.stdout + doctor.stderr
    assert doctor.returncode != 0
    assert AGE_CANARY not in blob
    assert "Traceback" not in blob


def test_incomplete_registry_record_fails_closed_no_traceback(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    registry = vault / "registry.json"
    registry.write_text(
        json.dumps({"format_version": 1, "identities": {"agent": {"id": "agent"}}})
        + "\n",
        encoding="utf-8",
    )
    proc = run_cli(["--json", "show"], env)
    blob = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "Traceback" not in blob
    assert AGE_CANARY not in blob
    err = json.loads(proc.stdout or proc.stderr)
    assert err["ok"] is False


def test_identity_access_drops_extra_keys_and_rejects_secret_recipient(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    log = MemoryLog()
    access = FileIdentityAccess(vault, log)
    registry = vault / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "format_version": 1,
                "identities": {
                    "agent": {
                        "id": "agent",
                        "recipient": AGE_CANARY,
                        "store_binding": "sops",
                        "private": PLAIN_CANARY,
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="cannot read registry.json") as caught:
        access.find("agent")
    assert AGE_CANARY not in str(caught.value)
    assert PLAIN_CANARY not in str(caught.value)


def test_identity_and_mail_strip_backend_secrets(vault, monkeypatch):
    app = build_app(vault)
    init_identity(app, monkeypatch)
    app.client.create("email.credential", TOKEN_CANARY)
    app.manager._mailboxes = _LeakMailboxFactory()
    view = app.client.identity()
    dumped = json.dumps(view)
    assert TOKEN_CANARY not in dumped
    assert AGE_CANARY not in dumped
    assert "credential" not in dumped
    assert "private_key" not in dumped
    assert view["email"]["address"] == "inbox@example.com"

    messages = app.client.email_receive()
    blob = json.dumps(messages)
    assert TOKEN_CANARY not in blob
    assert WALLET_CANARY not in blob
    assert "credential" not in blob
    assert messages[0]["body"] == "ok"


def test_wallet_views_and_cannot_send_do_not_passthrough_key(vault, monkeypatch):
    app = build_app(vault)
    init_identity(app, monkeypatch)
    app.manager._wallets = _LeakWalletFactory()
    view = app.client.identity()
    dumped = json.dumps(view)
    assert WALLET_CANARY not in dumped
    assert TOKEN_CANARY not in dumped
    assert "private_key" not in dumped

    bal = app.client.wallet_balance()
    assert WALLET_CANARY not in json.dumps(bal)
    assert "private_key" not in bal
    assert bal["asset"] == "USDC"

    with pytest.raises(CannotSend) as caught:
        app.client.wallet_send("0x" + "11" * 20, "1")
    assert WALLET_CANARY not in str(caught.value)
    assert str(caught.value) == "backend cannot send"


def test_cli_wallet_send_does_not_echo_backend_key(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    apply_cli_env(monkeypatch, env)
    monkeypatch.setattr(
        "agentself.compose.WalletAccessFactory.for_binding",
        lambda self, binding: _LeakWallet(),
    )
    code = main(["wallet", "send", "0x" + "11" * 20, "1"])
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    assert code == 2, blob
    assert WALLET_CANARY not in blob
    assert "Traceback" not in blob
    assert captured.err == "refused: backend cannot send\n"


def test_age_key_file_relative_escape_fails_closed(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.agekey"
    outside.write_text(AGE_CANARY + "\n", encoding="utf-8")
    assert resolve_age_key_file(vault, "../outside.agekey") == ""
    assert resolve_age_key_file(vault, "identities/agent/agent.agekey").endswith(
        str(Path("identities") / "agent" / "agent.agekey")
    )


def test_ensure_age_key_refuses_symlink(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    pdir = identity_home(vault, "agent")
    pdir.mkdir(parents=True)
    stolen = tmp_path / "stolen"
    key = pdir / "agent.agekey"
    symlink_or_skip(key, stolen)

    def boom(*args, **kwargs):
        raise AssertionError("age-keygen must not run on a symlink")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(IdentityStateError, match="age key file is not usable"):
        ensure_age_key(vault, "agent")
    assert not stolen.exists()
    assert key.is_symlink()


def test_safe_filename_does_not_allow_dotdot():
    assert _safe_filename("..") != ".."
    assert ".." not in _safe_filename("..")
    assert os.sep not in _safe_filename("../inbox_id")
    assert _safe_filename("msg_ok") == "msg_ok"


def test_resolve_tool_skips_current_directory(tmp_path, monkeypatch):
    planted = tmp_path / ("sops.exe" if os.name == "nt" else "sops")
    planted.write_text("not-the-real-sops", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    path_dir = tmp_path / "bin"
    path_dir.mkdir()
    real = path_dir / planted.name
    real.write_text("real-sops", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + str(path_dir))
    found = Path(resolve_tool("sops"))
    assert found.resolve() == real.resolve()
    assert found.resolve() != planted.resolve()


def test_leftover_secret_tmp_is_shredded_on_list(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    hold = secrets_home(vault, "P")
    hold.mkdir(parents=True)
    leftover = hold / "secret.leftover.tmp"
    leftover.write_text(PLAIN_CANARY, encoding="utf-8")
    monkeypatch.setattr(
        "agentself.backends.store.sops.run_cmd",
        lambda *a, **k: subprocess.CompletedProcess(["sops"], 0, b"", b""),
    )
    store = SopsStoreAccess(vault, MemoryLog())
    store.list("P")
    assert not leftover.exists()


def test_shred_unlink_overwrites_plaintext_and_does_not_follow_symlink(tmp_path):
    path = tmp_path / "secret.tmp"
    path.write_text(PLAIN_CANARY, encoding="utf-8")
    shred_unlink(path)
    assert not path.exists()

    target = tmp_path / "stolen"
    target.write_text(AGE_CANARY, encoding="utf-8")
    link = tmp_path / "agent.agekey"
    symlink_or_skip(link, target)
    shred_unlink(link)
    assert not link.exists()
    assert target.read_text(encoding="utf-8") == AGE_CANARY


def test_create_failed_shreds_plaintext_tmp(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    identity_id = "P"
    key = identity_home(vault, identity_id) / "agent.agekey"
    key.parent.mkdir(parents=True)
    key.write_bytes(b"dummy-age-key\n")
    seen: list[Path] = []

    def fake_run_cmd(argv, *, env=None, stdin=None, timeout=30):
        cmd = list(argv)
        if cmd[0] == "age-keygen":
            return subprocess.CompletedProcess(
                cmd, 0, stdout=b"age1testrecipientnotarealkey\n", stderr=b""
            )
        if cmd[0] == "sops":
            input_path = Path(cmd[-1])
            seen.append(input_path)
            assert input_path.read_text(encoding="utf-8") == PLAIN_CANARY
            return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"")
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr("agentself.backends.store.sops.run_cmd", fake_run_cmd)
    store = SopsStoreAccess(vault, MemoryLog())
    with pytest.raises(StoreResourceError, match="^create failed$") as caught:
        store.create(identity_id, "token", PLAIN_CANARY)
    assert PLAIN_CANARY not in str(caught.value)
    assert seen
    for path in seen:
        assert not path.exists()


def test_get_non_utf8_fails_closed_without_secret(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    identity_id = "P"
    key = identity_home(vault, identity_id) / "agent.agekey"
    hold = secrets_home(vault, identity_id)
    hold.mkdir(parents=True)
    key.write_bytes(b"dummy-age-key\n")
    entry = hold / "token.sops"
    entry.write_bytes(b"ciphertext")

    def fake_run_cmd(argv, *, env=None, stdin=None, timeout=30):
        cmd = list(argv)
        if cmd[0] == "sops":
            return subprocess.CompletedProcess(
                cmd, 0, stdout=b"\xff" + PLAIN_CANARY.encode(), stderr=b""
            )
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr("agentself.backends.store.sops.run_cmd", fake_run_cmd)
    store = SopsStoreAccess(vault, MemoryLog())
    with pytest.raises(StoreResourceError, match="^get failed$") as caught:
        store.get(identity_id, "token")
    assert PLAIN_CANARY not in str(caught.value)
    assert caught.value.__cause__ is None


def test_run_cmd_timeout_drops_stdout(monkeypatch):
    secret = PLAIN_CANARY

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=kwargs.get("args") or args[0],
            timeout=1,
            output=secret.encode(),
            stderr=secret.encode(),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(StoreResourceError, match="^store timeout$") as caught:
        run_cmd(["sops", "--decrypt", "token.sops"])
    assert caught.value.__cause__ is None
    assert secret not in str(caught.value)


def test_run_cmd_sets_windows_no_default_cwd_env(tmp_path, monkeypatch):
    planted = tmp_path / ("sops.exe" if os.name == "nt" else "sops")
    planted.write_text("not-the-real-sops", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    seen: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(argv, **kwargs):
        env = kwargs.get("env")
        seen.append((list(argv), None if env is None else dict(env)))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr("agentself.internal.files.subprocess.run", fake_run)
    run_cmd(["sops", "--version"])
    assert seen
    argv, env = seen[0]
    cmd0 = Path(argv[0])
    if cmd0.is_absolute() or len(cmd0.parts) > 1:
        assert cmd0.resolve() != planted.resolve()
    if os.name == "nt":
        assert env is not None
        assert env.get("NoDefaultCurrentDirectoryInExePath") == "1"


def test_require_secret_rejects_header_injection():
    with pytest.raises(MailboxError) as caught:
        require_secret("tok\r\nAuthorization: Bearer " + TOKEN_CANARY)
    assert TOKEN_CANARY not in str(caught.value)
    assert "tok" not in str(caught.value)
    assert str(caught.value) == "invalid credentials"
    with pytest.raises(MailboxError, match="missing credentials"):
        require_secret("")
    assert require_secret("ok-token") == "ok-token"


def test_hex_int_rejects_malicious_rpc_numbers():
    assert _hex_int("0xff") == 255
    assert _hex_int(0) == 0
    with pytest.raises(WalletError, match="rpc failed"):
        _hex_int("0x" + "f" * 10_000)
    with pytest.raises(WalletError, match="rpc failed"):
        _hex_int("not-a-key-" + PLAIN_CANARY)
    with pytest.raises(WalletError, match="rpc failed"):
        _hex_int(2**300)


def test_tx_known_rejects_truthy_non_receipt():
    from agentself.backends.wallet.base import BaseWalletAccess

    class YesRpc:
        def request(self, method: str, params: list[object]) -> object:
            return "yes"

    wallet = BaseWalletAccess(MemoryLog(), rpc=YesRpc())
    assert wallet._tx_known("0x" + "ab" * 32) is False


def test_wallet_send_log_hash_is_capped():
    huge = "0x" + "ab" * 200
    assert huge not in _ok_hash(huge)
    assert _ok_hash(huge) == "ok"
    digest = "0x" + "cd" * 32
    assert _ok_hash(digest) == f"ok {digest}"
    signed = SimpleNamespace(hash=bytes.fromhex("ee" * 32))
    assert _send_result(huge, signed) == "ok 0x" + "ee" * 32


def test_store_failure_message_does_not_include_value(vault, monkeypatch):
    app = build_app(vault)
    init_identity(app, monkeypatch)

    def boom(self, identity_id, name, value):
        raise StoreResourceError(f"create failed {value}")

    monkeypatch.setattr(
        "agentself.backends.store.sops.SopsStoreAccess.create",
        boom,
    )
    with pytest.raises(StoreFailure) as caught:
        app.client.create("notes", PLAIN_CANARY)
    assert PLAIN_CANARY not in str(caught.value)
    assert str(caught.value) == "store error"

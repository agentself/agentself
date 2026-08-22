from __future__ import annotations

import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agentself.backends.email.contract import MailboxAccess
from agentself.backends.email.factory import MailboxAccessFactory
from agentself.backends.store.contract import StoreAccess
from agentself.backends.store.factory import StoreAccessFactory
from agentself.backends.wallet.contract import WalletAccess
from agentself.backends.wallet.factory import WalletAccessFactory
from agentself.client import Gateway
from agentself.internal.custody.manager import CustodyManager
from agentself.internal.files import identity_home
from agentself.internal.log import MemoryLog
from agentself.internal.registry import FilePrincipalAccess
from agentself.internal.types import Principal
from agentself.local import ensure_age_key

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def plant_host_binaries(bin_dir: Path, *names: str) -> Path:
    """Copy PATH hits into bin_dir using each file's real name (Windows .exe)."""

    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        found = shutil.which(name)
        if found:
            src = Path(found).resolve()
            shutil.copy2(src, bin_dir / src.name)
    return bin_dir


def symlink_or_skip(link: Path, target: Path | str) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks not available: {exc}")


class InstrumentedPrincipalAccess:
    def __init__(self, inner: FilePrincipalAccess) -> None:
        self.inner = inner
        self.calls: list[tuple] = []

    def find(self, principal_id: str) -> Principal | None:
        self.calls.append(("find", principal_id))
        return self.inner.find(principal_id)

    def enroll(
        self, principal_id: str, recipient: str, store_binding: str
    ) -> Principal:
        self.calls.append(("enroll", principal_id, store_binding))
        return self.inner.enroll(principal_id, recipient, store_binding)


class InstrumentedStoreAccess:
    def __init__(self, inner: StoreAccess) -> None:
        self.inner = inner
        self.calls: list[tuple] = []

    def seal(self, principal_id: str, name: str, value: str) -> None:
        self.calls.append(("seal", principal_id, name))
        return self.inner.seal(principal_id, name, value)

    def reveal(self, principal_id: str, name: str) -> str:
        self.calls.append(("reveal", principal_id, name))
        return self.inner.reveal(principal_id, name)

    def replace(self, principal_id: str, name: str, value: str) -> None:
        self.calls.append(("replace", principal_id, name))
        return self.inner.replace(principal_id, name, value)

    def list(self, principal_id: str) -> list[str]:
        self.calls.append(("list", principal_id, None))
        return self.inner.list(principal_id)

    def delete(self, principal_id: str, name: str) -> None:
        self.calls.append(("delete", principal_id, name))
        return self.inner.delete(principal_id, name)


class InstrumentedStoreFactory:
    def __init__(self, inner: StoreAccessFactory) -> None:
        self.inner = inner
        self.for_binding_calls: list[str] = []
        self.instances: list[InstrumentedStoreAccess] = []

    def for_binding(self, binding: str) -> StoreAccess:
        self.for_binding_calls.append(binding)
        inst = InstrumentedStoreAccess(self.inner.for_binding(binding))
        self.instances.append(inst)
        return inst

    @property
    def calls(self) -> list[tuple]:
        out: list[tuple] = []
        for inst in self.instances:
            out.extend(inst.calls)
        return out


class InstrumentedMailboxAccess:
    def __init__(self, inner: MailboxAccess) -> None:
        self.inner = inner
        self.calls: list[tuple] = []

    def send(self, principal_id, to, subject, body, send_token=None, address=None):
        self.calls.append(("send", principal_id))
        return self.inner.send(
            principal_id,
            to,
            subject,
            body,
            send_token=send_token,
            address=address,
        )

    def recv(self, principal_id, *, send_token=None, address=None, message_id=None):
        self.calls.append(("recv", principal_id))
        return self.inner.recv(
            principal_id,
            send_token=send_token,
            address=address,
            message_id=message_id,
        )

    def list(self, principal_id, *, send_token=None, address=None):
        self.calls.append(("list", principal_id))
        return self.inner.list(principal_id, send_token=send_token, address=address)

    def describe(self, principal_id, *, send_token=None, address=None):
        self.calls.append(("describe", principal_id))
        return self.inner.describe(principal_id, send_token=send_token, address=address)

    def connect(self, principal_id, *, send_token=None, address=None):
        self.calls.append(("connect", principal_id))
        return self.inner.connect(principal_id, send_token=send_token, address=address)


class DoubleMailboxFactory:
    """Product factory plus a maildir test double. Double is not a catalog bind."""

    def __init__(self, inner: MailboxAccessFactory, vault: Path, log, domain: str = ""):
        self.inner = inner
        self._root = Path(vault)
        self._log = log
        self._domain = domain

    def for_binding(self, binding: str) -> MailboxAccess:
        if binding == "maildir":
            from tests.maildir_mailbox import MaildirMailboxAccess

            return MaildirMailboxAccess(self._root, self._log, domain=self._domain)
        return self.inner.for_binding(binding)


class InstrumentedMailboxFactory:
    def __init__(self, inner: MailboxAccessFactory) -> None:
        self.inner = inner
        self.for_binding_calls: list[str] = []
        self.instances: list[InstrumentedMailboxAccess] = []

    def for_binding(self, binding: str) -> MailboxAccess:
        self.for_binding_calls.append(binding)
        inst = InstrumentedMailboxAccess(self.inner.for_binding(binding))
        self.instances.append(inst)
        return inst

    @property
    def calls(self) -> list[tuple]:
        out: list[tuple] = []
        for inst in self.instances:
            out.extend(inst.calls)
        return out


class InstrumentedWalletAccess:
    def __init__(self, inner: WalletAccess) -> None:
        self.inner = inner
        self.calls: list[tuple] = []

    @property
    def needs_material(self) -> bool:
        return bool(getattr(self.inner, "needs_material", False))

    def bind_key(self, key_hex: str) -> None:
        binder = getattr(self.inner, "bind_key", None)
        if binder is not None:
            binder(key_hex)

    def address(self, principal_id):
        self.calls.append(("address",))
        return self.inner.address(principal_id)

    def sign(self, principal_id, message):
        self.calls.append(("sign",))
        return self.inner.sign(principal_id, message)

    def balance(self, principal_id):
        self.calls.append(("balance",))
        return self.inner.balance(principal_id)

    def send(self, principal_id, to, amount, asset):
        self.calls.append(("send",))
        return self.inner.send(principal_id, to, amount, asset)

    def describe(self, principal_id):
        self.calls.append(("describe",))
        return self.inner.describe(principal_id)


class InstrumentedWalletFactory:
    def __init__(self, inner: WalletAccessFactory) -> None:
        self.inner = inner
        self.for_binding_calls: list[str] = []
        self.instances: list[InstrumentedWalletAccess] = []

    def for_binding(self, binding: str) -> WalletAccess:
        self.for_binding_calls.append(binding)
        inst = InstrumentedWalletAccess(self.inner.for_binding(binding))
        self.instances.append(inst)
        return inst

    @property
    def calls(self) -> list[tuple]:
        out: list[tuple] = []
        for inst in self.instances:
            out.extend(inst.calls)
        return out


class MockRpc:
    """Test RPC. Default tests never hit the network."""

    def __init__(self, eth_wei: int = 0, usdc_raw: int = 0) -> None:
        self.eth_wei = eth_wei
        self.usdc_raw = usdc_raw
        self.calls: list[tuple] = []
        self.broadcast = False
        self.sent_raw: list[str] = []

    def request(self, method: str, params: list[object]) -> object:
        if method == "eth_sendRawTransaction":
            self.calls.append((method, ["0x"]))
            self.broadcast = True
            raw = str(params[0] if params else "")
            self.sent_raw.append(raw)
            return "0x" + "ab" * 32
        if method == "eth_getTransactionByHash":
            self.calls.append((method, params))
            if self.broadcast:
                return {"hash": params[0] if params else "0x"}
            return None
        self.calls.append((method, params))
        if method == "eth_getBalance":
            return hex(self.eth_wei)
        if method == "eth_call":
            return "0x" + format(self.usdc_raw, "x").zfill(64)
        if method == "eth_getTransactionCount":
            return "0x0"
        if method == "eth_gasPrice":
            return "0x3b9aca00"
        if method == "eth_estimateGas":
            return "0x186a0"
        if method == "eth_chainId":
            return "0x2105"
        raise AssertionError(f"unexpected rpc method {method}")


class FakeRpcOpener:
    """Records URLs. Never opens a socket."""

    def __init__(self, *, usdc_raw: int = 0, eth_wei: int = 0) -> None:
        self.usdc_raw = usdc_raw
        self.eth_wei = eth_wei
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []
        self._status: dict[str, int | str] = {}
        self._default: int | str | None = None

    def ok(self, url: str) -> None:
        self._status[url] = "ok"

    def fail(self, url: str, status: int = 403) -> None:
        self._status[url] = status

    def fail_all(self, status: int = 403) -> None:
        self._default = status

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.urls.append(url)
        hdrs: dict[str, str] = {}
        items = getattr(req, "header_items", None)
        if callable(items):
            hdrs.update({str(k): str(v) for k, v in items()})
        else:
            for attr in ("headers", "unredirected_hdrs"):
                src = getattr(req, attr, None) or {}
                hdrs.update({str(k): str(v) for k, v in dict(src).items()})
        self.headers.append(hdrs)
        spec = self._status.get(url, self._default)
        if spec is None:
            raise AssertionError(f"unexpected rpc url {url}")
        if spec != "ok":
            raise urllib.error.HTTPError(
                url, int(spec), "Forbidden", None, io.BytesIO(b"")
            )
        method = json.loads(req.data or b"{}").get("method", "")
        if method == "eth_call":
            result = "0x" + format(self.usdc_raw, "x").zfill(64)
        elif method == "eth_getBalance":
            result = hex(self.eth_wei)
        else:
            result = "0x0"
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()
        return io.BytesIO(body)


@dataclass
class App:
    vault: Path
    log: MemoryLog
    principals: InstrumentedPrincipalAccess
    stores: InstrumentedStoreFactory
    mailboxes: InstrumentedMailboxFactory
    wallets: InstrumentedWalletFactory
    manager: CustodyManager
    gateway: Gateway
    rpc: MockRpc | None
    keys: dict[str, Path] = field(default_factory=dict)

    def bind(self, monkeypatch, principal_id: str) -> None:
        monkeypatch.setenv("AGENTSELF_IDENTITY_ID", principal_id)
        monkeypatch.setenv("AGE_KEY_FILE", str(self.keys[principal_id]))
        monkeypatch.setenv("AGENTSELF_VAULT_ROOT", str(self.vault))


def setup_principal(vault: Path, principal_id: str, store: str = "sops") -> Path:
    """Host keygen for tests. Shares ensure_age_key; does not exec a .sh file."""

    if store == "pass":
        _require_pass_host()
    key = ensure_age_key(vault, principal_id, store=store)
    if not key.is_file():
        raise RuntimeError("setup-principal did not write agent.agekey")
    if os.name != "nt":
        mode = key.stat().st_mode & 0o777
        if mode != 0o600:
            raise RuntimeError(f"agent.agekey mode is {oct(mode)}, expected 0o600")
    return key


def _require_pass_host() -> None:
    missing = [name for name in ("gpg", "pass") if shutil.which(name) is None]
    if missing:
        pytest.skip("pass store requires " + " and ".join(missing) + " on PATH")


def build_app(
    vault: Path,
    *,
    email_backend: str = "maildir",
    wallet_backend: str = "base",
    mail_domain: str = "",
    rpc: MockRpc | None = None,
    eth_rpc_url: str = "",
    rpc_opener=None,
) -> App:
    """Instrumented app. Defaults use tests/ doubles, not the public catalog."""
    log = MemoryLog()
    principals = InstrumentedPrincipalAccess(FilePrincipalAccess(vault, log))
    stores = InstrumentedStoreFactory(StoreAccessFactory(vault, log))
    mailboxes = InstrumentedMailboxFactory(
        DoubleMailboxFactory(
            MailboxAccessFactory(vault, log, domain=mail_domain),
            vault,
            log,
            mail_domain,
        )
    )
    if rpc is not None:
        injected: MockRpc | None = rpc
    elif rpc_opener is not None:
        injected = None
    else:
        injected = MockRpc()
    wallets = InstrumentedWalletFactory(
        WalletAccessFactory(
            log,
            rpc=injected,
            eth_rpc_url=eth_rpc_url,
            vault_root=vault,
            rpc_opener=rpc_opener,
        )
    )
    manager = CustodyManager(
        principals,
        stores,
        log,
        mailboxes=mailboxes,
        wallets=wallets,
        email_backend=email_backend,
        wallet_backend=wallet_backend,
    )
    gateway = Gateway(manager, log)
    return App(
        vault=vault,
        log=log,
        principals=principals,
        stores=stores,
        mailboxes=mailboxes,
        wallets=wallets,
        manager=manager,
        gateway=gateway,
        rpc=injected,
    )


def plant_email(
    vault: Path,
    principal_id: str,
    *,
    from_addr: str,
    subject: str,
    body: str,
    to: str = "",
) -> Path:
    new_dir = identity_home(vault, principal_id) / "maildir" / "new"
    new_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = new_dir / f"planted.{secrets.token_hex(4)}"
    dest = to or f"{principal_id}@local"
    path.write_text(
        f"From: {from_addr}\nTo: {dest}\nSubject: {subject}\n\n{body}\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def cli_env(vault: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["AGENTSELF_VAULT_ROOT"] = str(vault)
    pythonpath = env.get("PYTHONPATH", "")
    src = str(PROJECT_ROOT)
    env["PYTHONPATH"] = src + os.pathsep + pythonpath if pythonpath else src
    tools = Path(vault).parent / "agentself-tools"
    tools.mkdir(parents=True, exist_ok=True)
    env["AGENTSELF_TOOLS"] = str(tools)
    env["AGENTSELF_FETCH_TOOLS"] = "0"
    env["AGENTSELF_FORBID_LIVE_AGENTMAIL"] = "1"
    for key in (
        "AGENTSELF_IDENTITY_ID",
        "AGE_KEY_FILE",
        "AGENTSELF_MAIL_DOMAIN",
        "AGENTSELF_EMAIL_BACKEND",
        "AGENTSELF_WALLET_BACKEND",
    ):
        env.pop(key, None)
    return env


def run_cli(
    args: list[str],
    env: dict[str, str],
    input: str | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentself", *args],
        cwd=str(cwd) if cwd is not None else str(PROJECT_ROOT),
        env=env,
        input=input,
        capture_output=True,
        text=True,
        timeout=60,
    )


def enroll_principal(
    app: App, monkeypatch, principal_id: str = "P", store: str = "sops"
):
    app.keys[principal_id] = setup_principal(app.vault, principal_id, store=store)
    app.bind(monkeypatch, principal_id)
    app.gateway.enroll(store)


def apply_cli_env(monkeypatch, env: dict[str, str]) -> None:
    monkeypatch.setenv("AGENTSELF_VAULT_ROOT", env["AGENTSELF_VAULT_ROOT"])
    monkeypatch.setenv("PATH", env["PATH"])
    if "AGENTSELF_TOOLS" in env:
        monkeypatch.setenv("AGENTSELF_TOOLS", env["AGENTSELF_TOOLS"])
    if "AGENTSELF_FETCH_TOOLS" in env:
        monkeypatch.setenv("AGENTSELF_FETCH_TOOLS", env["AGENTSELF_FETCH_TOOLS"])
    if "AGENTSELF_FORBID_LIVE_AGENTMAIL" in env:
        monkeypatch.setenv(
            "AGENTSELF_FORBID_LIVE_AGENTMAIL", env["AGENTSELF_FORBID_LIVE_AGENTMAIL"]
        )
    for key in (
        "AGENTSELF_EMAIL_BACKEND",
        "AGENTSELF_WALLET_BACKEND",
        "AGENTSELF_MAIL_DOMAIN",
        "AGENTSELF_IDENTITY_ID",
        "AGE_KEY_FILE",
    ):
        monkeypatch.delenv(key, raising=False)


def compose_with_rpc(monkeypatch, rpc) -> None:
    from agentself.compose import compose as real

    def wrapped(*args, **kwargs):
        kwargs.setdefault("rpc", rpc)
        return real(*args, **kwargs)

    monkeypatch.setattr("agentself.cli.app.compose", wrapped, raising=False)


FEATURED_HELPS = [
    ["--help"],
    ["init", "--help"],
    ["show", "--help"],
    ["backends", "--help"],
    ["diagnose", "--help"],
    ["secret", "--help"],
    ["secret", "create", "--help"],
    ["secret", "get", "--help"],
    ["secret", "update", "--help"],
    ["secret", "list", "--help"],
    ["secret", "delete", "--help"],
    ["email", "--help"],
    ["email", "connect", "--help"],
    ["email", "show", "--help"],
    ["email", "send", "--help"],
    ["email", "receive", "--help"],
    ["email", "list", "--help"],
    ["wallet", "--help"],
    ["wallet", "show", "--help"],
    ["wallet", "address", "--help"],
    ["wallet", "balance", "--help"],
    ["wallet", "authorize", "--help"],
    ["wallet", "send", "--help"],
    ["backup", "--help"],
    ["restore", "--help"],
    ["install", "--help"],
]

ALIAS_HELPS: list[list[str]] = []

CLI_HELPS = FEATURED_HELPS + ALIAS_HELPS

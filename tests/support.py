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
from agentself.client import Client
from agentself.host import CHANNELS
from agentself.internal.custody.manager import CustodyManager
from agentself.internal.files import identity_home
from agentself.internal.log import MemoryLog
from agentself.internal.registry import FileIdentityAccess
from agentself.internal.types import Identity
from agentself.local import ensure_age_key

from tests.maildir_mailbox import MaildirMailboxAccess
from tests.synthetic_email import SyntheticEmailAccess
from tests.synthetic_store import MemoryStoreAccess
from tests.synthetic_wallet import SyntheticWalletAccess

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def value_file(folder: Path, text: str, name: str = "value.txt") -> str:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return str(path)


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


class InstrumentedIdentityAccess:
    def __init__(self, inner: FileIdentityAccess) -> None:
        self.inner = inner
        self.calls: list[tuple] = []

    def find(self, identity_id: str) -> Identity | None:
        self.calls.append(("find", identity_id))
        return self.inner.find(identity_id)

    def init(self, identity_id: str, recipient: str, store_binding: str) -> Identity:
        self.calls.append(("init", identity_id, store_binding))
        return self.inner.init(identity_id, recipient, store_binding)

    def add_wallet_material_name(self, identity_id: str, name: str) -> Identity:
        self.calls.append(("add_wallet_material_name", identity_id, name))
        return self.inner.add_wallet_material_name(identity_id, name)


class InstrumentedStoreAccess:
    def __init__(self, inner: StoreAccess) -> None:
        self.inner = inner
        self.calls: list[tuple] = []

    def prepare(self, identity_id: str) -> None:
        self.calls.append(("prepare", identity_id, None))
        return self.inner.prepare(identity_id)

    def required_tools(self):
        return self.inner.required_tools()

    def create(self, identity_id: str, name: str, value: str) -> None:
        self.calls.append(("create", identity_id, name))
        return self.inner.create(identity_id, name, value)

    def get(self, identity_id: str, name: str) -> str:
        self.calls.append(("get", identity_id, name))
        return self.inner.get(identity_id, name)

    def update(self, identity_id: str, name: str, value: str) -> None:
        self.calls.append(("update", identity_id, name))
        return self.inner.update(identity_id, name, value)

    def list(self, identity_id: str) -> list[str]:
        self.calls.append(("list", identity_id, None))
        return self.inner.list(identity_id)

    def delete(self, identity_id: str, name: str) -> None:
        self.calls.append(("delete", identity_id, name))
        return self.inner.delete(identity_id, name)


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
        return [call for inst in self.instances for call in inst.calls]


class InstrumentedMailboxAccess:
    def __init__(self, inner: MailboxAccess) -> None:
        self.inner = inner
        self.calls: list[tuple] = []

    def send(self, identity_id, to, subject, body, credential=None, address=None):
        self.calls.append(("send", identity_id))
        return self.inner.send(
            identity_id,
            to,
            subject,
            body,
            credential=credential,
            address=address,
        )

    def receive(self, identity_id, *, credential=None, address=None, message_id=None):
        self.calls.append(("receive", identity_id))
        return self.inner.receive(
            identity_id,
            credential=credential,
            address=address,
            message_id=message_id,
        )

    def list(self, identity_id, *, credential=None, address=None):
        self.calls.append(("list", identity_id))
        return self.inner.list(identity_id, credential=credential, address=address)

    def describe(self, identity_id, *, credential=None, address=None):
        self.calls.append(("describe", identity_id))
        return self.inner.describe(identity_id, credential=credential, address=address)

    def connect(
        self,
        identity_id,
        *,
        credential=None,
        address=None,
        answers=None,
        state=None,
    ):
        self.calls.append(("connect", identity_id))
        return self.inner.connect(
            identity_id,
            credential=credential,
            address=address,
            answers=answers,
            state=state,
        )

    def setup_options(self):
        return self.inner.setup_options()


class DoubleMailboxFactory:
    """Product factory plus a maildir test double. Double is not a catalog bind."""

    def __init__(self, inner: MailboxAccessFactory, vault: Path, log, domain: str = ""):
        self.inner = inner
        self._root = Path(vault)
        self._log = log
        self._domain = domain

    def for_binding(self, binding: str) -> MailboxAccess:
        if binding == "maildir":
            return MaildirMailboxAccess(self._root, self._log, domain=self._domain)
        if binding == "oauthish":
            return SyntheticEmailAccess()
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
        return [call for inst in self.instances for call in inst.calls]


class DoubleStoreFactory:
    """Product factory plus a memory test double. Double is not a catalog bind."""

    def __init__(self, inner: StoreAccessFactory) -> None:
        self.inner = inner
        self._memory: dict[tuple[str, str], str] = {}

    def for_binding(self, binding: str) -> StoreAccess:
        if binding == "memory":
            return MemoryStoreAccess(self._memory)
        return self.inner.for_binding(binding)


class DoubleWalletFactory:
    """Product factory plus a synthetic test double. Double is not a catalog bind."""

    def __init__(self, inner: WalletAccessFactory) -> None:
        self.inner = inner

    def for_binding(self, binding: str) -> WalletAccess:
        if binding == "synthetic":
            return SyntheticWalletAccess()
        return self.inner.for_binding(binding)


class InstrumentedWalletAccess:
    def __init__(self, inner: WalletAccess) -> None:
        self.inner = inner
        self.calls: list[tuple] = []

    def required_material(self):
        self.calls.append(("required_material",))
        return self.inner.required_material()

    def create_material(self):
        self.calls.append(("create_material",))
        return self.inner.create_material()

    def bind_material(self, value: str) -> None:
        self.calls.append(("bind_material", value))
        self.inner.bind_material(value)

    def address(self, identity_id):
        self.calls.append(("address",))
        return self.inner.address(identity_id)

    def authorize(self, identity_id, message):
        self.calls.append(("authorize",))
        return self.inner.authorize(identity_id, message)

    def verify(self, identity_id, message, authorization):
        self.calls.append(("verify",))
        return self.inner.verify(identity_id, message, authorization)

    def balance(self, identity_id):
        self.calls.append(("balance",))
        return self.inner.balance(identity_id)

    def send(self, identity_id, to, amount, asset):
        self.calls.append(("send",))
        return self.inner.send(identity_id, to, amount, asset)

    def describe(self, identity_id):
        self.calls.append(("describe",))
        return self.inner.describe(identity_id)


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
        return [call for inst in self.instances for call in inst.calls]


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
        self.headers.append({str(k): str(v) for k, v in req.header_items()})
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
    identities: InstrumentedIdentityAccess
    stores: InstrumentedStoreFactory
    mailboxes: InstrumentedMailboxFactory
    wallets: InstrumentedWalletFactory
    manager: CustodyManager
    client: Client
    rpc: MockRpc | None
    keys: dict[str, Path] = field(default_factory=dict)

    def bind(self, monkeypatch, identity_id: str) -> None:
        monkeypatch.setenv("AGENTSELF_IDENTITY_ID", identity_id)
        monkeypatch.setenv("AGE_KEY_FILE", str(self.keys[identity_id]))
        monkeypatch.setenv("AGENTSELF_IDENTITY_DIR", str(self.vault))


def setup_identity(vault: Path, identity_id: str, store: str = "sops") -> Path:
    """Host keygen for tests. Shares ensure_age_key; does not exec a .sh file."""

    if store == "pass":
        _require_pass_host()
    key = ensure_age_key(vault, identity_id)
    if store == "pass":
        from agentself.backends.store.passstore import PassStoreAccess

        PassStoreAccess(vault, MemoryLog()).prepare(identity_id)
    if not key.is_file():
        raise RuntimeError("setup-identity did not write agent.agekey")
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
    allowed = frozenset((*CHANNELS["store"].names, "memory"))
    identities = InstrumentedIdentityAccess(
        FileIdentityAccess(vault, log, allowed_bindings=allowed)
    )
    stores = InstrumentedStoreFactory(
        DoubleStoreFactory(StoreAccessFactory(vault, log))
    )
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
        DoubleWalletFactory(
            WalletAccessFactory(
                log,
                rpc=injected,
                eth_rpc_url=eth_rpc_url,
                vault_root=vault,
                rpc_opener=rpc_opener,
            )
        )
    )
    manager = CustodyManager(
        identities,
        stores,
        log,
        mailboxes=mailboxes,
        wallets=wallets,
        email_backend=email_backend,
        wallet_backend=wallet_backend,
        allowed_store_bindings=allowed,
    )
    client = Client(manager, log)
    return App(
        vault=vault,
        log=log,
        identities=identities,
        stores=stores,
        mailboxes=mailboxes,
        wallets=wallets,
        manager=manager,
        client=client,
        rpc=injected,
    )


def plant_email(
    vault: Path,
    identity_id: str,
    *,
    from_addr: str,
    subject: str,
    body: str,
    to: str = "",
) -> Path:
    new_dir = identity_home(vault, identity_id) / "maildir" / "new"
    new_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = new_dir / f"planted.{secrets.token_hex(4)}"
    dest = to or f"{identity_id}@local"
    path.write_text(
        f"From: {from_addr}\nTo: {dest}\nSubject: {subject}\n\n{body}\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def cli_env(vault: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["AGENTSELF_IDENTITY_DIR"] = str(vault)
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
        "AGENTSELF_EMAIL_ADDRESS",
        "AGENTSELF_EMAIL_CREDENTIAL",
        "AGENTSELF_AGENTMAIL_API_KEY",
        "AGENTSELF_MAIL_PASSWORD",
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


def init_identity(app: App, monkeypatch, identity_id: str = "P", store: str = "sops"):
    app.keys[identity_id] = setup_identity(app.vault, identity_id, store=store)
    app.bind(monkeypatch, identity_id)
    app.client.init(store)


def apply_cli_env(monkeypatch, env: dict[str, str]) -> None:
    monkeypatch.setenv("AGENTSELF_IDENTITY_DIR", env["AGENTSELF_IDENTITY_DIR"])
    monkeypatch.setenv("PATH", env["PATH"])
    for key in (
        "AGENTSELF_TOOLS",
        "AGENTSELF_FETCH_TOOLS",
        "AGENTSELF_FORBID_LIVE_AGENTMAIL",
        "HOME",
        "USERPROFILE",
    ):
        if key in env:
            monkeypatch.setenv(key, env[key])
    for key in (
        "AGENTSELF_EMAIL_BACKEND",
        "AGENTSELF_WALLET_BACKEND",
        "AGENTSELF_MAIL_DOMAIN",
        "AGENTSELF_IDENTITY_ID",
        "AGE_KEY_FILE",
        "AGENTSELF_EMAIL_ADDRESS",
        "AGENTSELF_EMAIL_CREDENTIAL",
        "AGENTSELF_AGENTMAIL_API_KEY",
        "AGENTSELF_MAIL_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)


def compose_with_rpc(monkeypatch, rpc) -> None:
    from agentself.compose import compose as real

    def wrapped(*args, **kwargs):
        kwargs.setdefault("rpc", rpc)
        return real(*args, **kwargs)

    monkeypatch.setattr("agentself.cli.app.compose", wrapped, raising=False)

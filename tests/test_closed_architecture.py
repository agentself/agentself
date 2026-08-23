"""Architectural fitness functions for the closed ports-and-adapters layout.

Intended call graph:

    CLI → Client → CustodyManager → channel contracts / factories → backends

compose.py is the composition root. It is the only module that may import
factories and construct concrete backends.

These tests encode dependency direction and substitution, not file size or
folder counts. Adding a backend for an existing channel should not require
CLI commands, Client methods, manager methods, or unrelated backends.

Keep:

* CLI, Client, and __main__ do not import backends or vendor SDKs.
* Backends do not import each other, the Client, the CLI, or the manager.
* The manager imports channel contracts, not implementations or factories.
* Factories return the channel contract.
* Contracts and factories do not import vendor types.
* compose.py wires factories; the CLI does not import backends.
* Secret commands do not select a store implementation.

Do not encode as architecture:

* line counts, class counts, or factory file length
* packaging layout (src/ vs repo root)
* historical removed-backend names
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import get_type_hints

from tests.support import PROJECT_ROOT

PKG = PROJECT_ROOT / "agentself"
BACKENDS = PKG / "backends"
INTERNAL = PKG / "internal"
CLI = PKG / "cli"

CHANNELS = frozenset({"wallet", "email", "store"})
CLIENT_ROOTS = (
    "agentself.client",
    "agentself.bind",
    "agentself.local",
    "agentself.host",
    "agentself.cli",
    "agentself.__main__",
)
MANAGER_ROOTS = ("agentself.internal.custody",)
VENDOR_ROOTS = (
    "eth_account",
    "eth_utils",
    "web3",
    "sops",
    "age",
    "subprocess",
    "urllib",
    "urllib3",
    "requests",
    "resend",
    "cloudflare",
    "stripe",
    "twilio",
    "imaplib",
    "smtplib",
)


def _imported_modules(rel: str | Path) -> set[str]:
    path = PROJECT_ROOT / rel if not isinstance(rel, Path) else rel
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _is_under(name: str, root: str) -> bool:
    return name == root or name.startswith(root + ".")


def _mentions(names: set[str], *roots: str) -> bool:
    return any(_is_under(name, root) for name in names for root in roots)


def _py_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py") if "__pycache__" not in path.parts
    )


def _channel_dirs(channel: str) -> set[str]:
    root = BACKENDS / channel
    return {
        path.name
        for path in root.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }


def _is_channel_factory(name: str) -> bool:
    if not name.startswith("agentself.backends."):
        return False
    rest = name[len("agentself.backends.") :]
    parts = rest.split(".")
    return len(parts) >= 2 and parts[1] == "factory"


def test_client_cli_and_main_do_not_import_backends_or_vendor_sdks():
    files = [PKG / "client.py", PKG / "__main__.py", *_py_files(CLI)]
    for path in files:
        names = _imported_modules(path)
        assert not _mentions(names, "agentself.backends"), path
        assert not _mentions(names, *VENDOR_ROOTS), path


def test_host_does_not_import_backends():
    names = _imported_modules(PKG / "host.py")
    assert not _mentions(names, "agentself.backends"), names
    assert "agentself.email_catalog" in names


def test_email_adapters_use_neutral_option_catalog():
    for path in (
        BACKENDS / "email" / "agentmail" / "__init__.py",
        BACKENDS / "email" / "imap" / "__init__.py",
    ):
        names = _imported_modules(path)
        assert "agentself.email_catalog" in names, path
        assert not _mentions(
            names,
            "agentself.backends.email.agentmail_options",
            "agentself.backends.email.imap_options",
        ), path


def test_host_and_help_do_not_load_email_adapters():
    script = (
        "import json, sys\n"
        "banned = [\n"
        "    'imaplib', 'smtplib',\n"
        "    'agentself.backends.email.http',\n"
        "    'agentself.backends.email.agentmail',\n"
        "    'agentself.backends.email.imap',\n"
        "]\n"
        "def loaded():\n"
        "    return sorted(name for name in sys.modules if name == 'agentself.backends' or name.startswith('agentself.backends.'))\n"
        "import agentself.host\n"
        "assert loaded() == [], loaded()\n"
        "assert 'agentself.email_catalog' in sys.modules\n"
        "from agentself.cli.app import main\n"
        "try:\n"
        "    code = main(['--help'])\n"
        "except SystemExit as exc:\n"
        "    code = exc.code\n"
        "assert code in (0, None), code\n"
        "assert loaded() == [], loaded()\n"
        "print(json.dumps({'ok': True}))\n"
    )
    merged = os.environ.copy()
    src = str(PROJECT_ROOT)
    pythonpath = merged.get("PYTHONPATH", "")
    merged["PYTHONPATH"] = src + os.pathsep + pythonpath if pythonpath else src
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        env=merged,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout.splitlines()[-1]) == {"ok": True}


def test_backends_do_not_import_each_other_client_or_manager():
    for channel in CHANNELS:
        for path in _py_files(BACKENDS / channel):
            names = _imported_modules(path)
            assert not _mentions(names, *MANAGER_ROOTS), path
            assert not _mentions(names, *CLIENT_ROOTS), path
            assert not _mentions(names, "agentself.internal.registry"), path
            for other in CHANNELS:
                if other == channel:
                    continue
                assert not _mentions(names, f"agentself.backends.{other}"), path


def test_manager_imports_contracts_not_implementations():
    banned = (
        "agentself.client",
        "agentself.cli",
        "agentself.compose",
        "agentself.host",
    )
    for path in _py_files(INTERNAL / "custody"):
        names = _imported_modules(path)
        assert not _mentions(names, *banned), path
        for channel in CHANNELS:
            prefix = f"agentself.backends.{channel}"
            for name in names:
                if not _is_under(name, prefix):
                    continue
                rest = name[len(prefix) :].lstrip(".")
                assert rest == "contract" or rest.startswith("contract."), (
                    f"{path} imported backend implementation {name}"
                )


def test_only_compose_imports_channel_factories():
    compose = PKG / "compose.py"
    for path in _py_files(PKG):
        if path.is_relative_to(BACKENDS):
            continue
        imported = [
            name for name in _imported_modules(path) if _is_channel_factory(name)
        ]
        if path == compose:
            assert imported, path
            continue
        assert not imported, f"{path} imported channel factory {imported}"


def test_registry_does_not_import_store_manager_or_client():
    names = _imported_modules(INTERNAL / "registry.py")
    assert not _mentions(names, "agentself.backends")
    assert not _mentions(names, *MANAGER_ROOTS)
    assert not _mentions(names, *CLIENT_ROOTS)


def test_compose_is_the_composition_root():
    names = _imported_modules(PKG / "compose.py")
    assert _mentions(names, "agentself.backends.wallet.factory")
    assert _mentions(names, "agentself.backends.email.factory")
    assert _mentions(names, "agentself.backends.store.factory")
    assert _mentions(names, "agentself.internal.custody")
    assert not _mentions(names, "agentself.cli")
    for path in (PKG / "__main__.py", *_py_files(CLI)):
        imported = _imported_modules(path)
        assert not _mentions(imported, "agentself.backends"), path


def test_factories_return_the_contract():
    from agentself.backends.email.contract import MailboxAccess
    from agentself.backends.email.factory import MailboxAccessFactory
    from agentself.backends.store.contract import StoreAccess
    from agentself.backends.store.factory import StoreAccessFactory
    from agentself.backends.wallet.contract import WalletAccess
    from agentself.backends.wallet.factory import WalletAccessFactory

    for factory, contract in (
        (StoreAccessFactory, StoreAccess),
        (MailboxAccessFactory, MailboxAccess),
        (WalletAccessFactory, WalletAccess),
    ):
        hints = get_type_hints(factory.for_binding)
        assert hints["return"] is contract, factory


def test_contracts_and_factories_have_no_vendor_types():
    paths = [INTERNAL / "registry.py"]
    for channel in CHANNELS:
        paths.append(BACKENDS / channel / "contract.py")
        paths.append(BACKENDS / channel / "factory.py")
    for path in paths:
        names = _imported_modules(path)
        assert not _mentions(names, *VENDOR_ROOTS), path


def test_wallet_contract_stays_provider_neutral():
    contract = (BACKENDS / "wallet" / "contract.py").read_text(encoding="utf-8")
    assert "key_hex" not in contract
    assert "NoEthForGas" not in contract
    assert "USDC" not in contract
    assert "ETH" not in contract


def test_secret_commands_do_not_select_a_store():
    from agentself.cli.parser import _parser
    from agentself.host import CHANNELS as catalog

    assert catalog["store"].flag == "--store"
    commands = _parser()._subparsers._group_actions[0].choices
    init_flags = {
        option
        for action in commands["init"]._actions
        for option in action.option_strings
    }
    assert "--store" in init_flags
    secret_cmds = commands["secret"]._subparsers._group_actions[0].choices
    for name in ("create", "get", "update", "delete"):
        flags = {
            option
            for action in secret_cmds[name]._actions
            for option in action.option_strings
        }
        assert "--store" not in flags


def test_backend_folders_are_wired_by_factories():
    found = {
        path.name
        for path in BACKENDS.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert found == CHANNELS
    for channel in CHANNELS:
        root = BACKENDS / channel
        assert (root / "contract.py").is_file()
        assert (root / "factory.py").is_file()
        folders = _channel_dirs(channel)
        imported: set[str] = set()
        prefix = f"agentself.backends.{channel}."
        for name in _imported_modules(root / "factory.py"):
            if not name.startswith(prefix):
                continue
            part = name[len(prefix) :].split(".", 1)[0]
            if (root / part).is_dir():
                imported.add(part)
        assert imported == folders, (channel, imported, folders)


def test_channel_resource_access_is_not_under_internal():
    found = {
        path.name
        for path in INTERNAL.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert found.isdisjoint(CHANNELS)
    assert "custody" in found
    assert (INTERNAL / "registry.py").is_file()


def test_main_reexports_cli_and_does_not_compose():
    names = _imported_modules(PKG / "__main__.py")
    assert "agentself.cli.app" in names
    assert not _mentions(names, "agentself.compose")
    assert not _mentions(names, "agentself.backends")
    assert not _mentions(names, *MANAGER_ROOTS)

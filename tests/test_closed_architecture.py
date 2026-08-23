"""Closed call graph: Client does not import backends; backends do not import each other or the manager.

IDesign stays as rules, not folder names. Folders follow rclone: one directory per shipped backend.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_type_hints

from tests.support import PROJECT_ROOT

PKG = PROJECT_ROOT / "agentself"
BACKENDS = PKG / "backends"
INTERNAL = PKG / "internal"
CLI = PKG / "cli"

RA_MODULES = (
    "agentself.backends.wallet",
    "agentself.backends.email",
    "agentself.backends.store",
    "agentself.internal.registry",
)

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

ALLOWED_INTERNAL = frozenset({"custody"})
CHANNEL_FOLDERS = frozenset({"wallet", "email", "store"})
WALLET_BACKEND_FOLDERS = frozenset({"base", "ethereum"})
EMAIL_BACKEND_FOLDERS = frozenset({"agentmail", "imap"})
STORE_BACKEND_FOLDERS = frozenset({"sops", "passstore"})
FACTORY_GOD_FILE_LINES = 150


def _imported_modules(rel: str | Path) -> set[str]:
    path = PROJECT_ROOT / rel if not isinstance(rel, Path) else rel
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
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


def test_client_does_not_import_backends():
    names = _imported_modules(PKG / "client.py")
    for pkg in RA_MODULES:
        assert not _mentions(names, pkg), f"client imported {pkg}: {names}"


def test_registry_does_not_import_store_or_manager():
    names = _imported_modules(INTERNAL / "registry.py")
    assert not _mentions(names, "agentself.backends.store")
    assert not _mentions(names, *MANAGER_ROOTS)
    assert not _mentions(names, *CLIENT_ROOTS)


def test_backends_do_not_import_each_other_or_manager():
    for channel in CHANNEL_FOLDERS:
        root = BACKENDS / channel
        for path in _py_files(root):
            names = _imported_modules(path)
            assert not _mentions(names, *MANAGER_ROOTS), path
            assert not _mentions(names, *CLIENT_ROOTS), path
            assert not _mentions(names, "agentself.internal.registry"), path
            for other in CHANNEL_FOLDERS:
                if other == channel:
                    continue
                assert not _mentions(names, f"agentself.backends.{other}"), (
                    f"{path} imported {other}"
                )


def test_secret_cli_does_not_take_store_flag():
    from agentself.cli.parser import _parser
    from agentself.host import CHANNELS

    assert CHANNELS["store"].flag == "--store"
    parser = _parser()
    commands = parser._subparsers._group_actions[0].choices
    init_flags = {
        option
        for action in commands["init"]._actions
        for option in action.option_strings
    }
    assert "--store" in init_flags
    secret = commands["secret"]
    secret_cmds = secret._subparsers._group_actions[0].choices
    for name in ("create", "get", "update", "delete"):
        flags = {
            option
            for action in secret_cmds[name]._actions
            for option in action.option_strings
        }
        assert "--store" not in flags
    src = (CLI / "parser.py").read_text(encoding="utf-8")
    assert "openbao" not in src.lower()


def test_every_backend_file_is_closed():
    for channel in CHANNEL_FOLDERS:
        for path in _py_files(BACKENDS / channel):
            names = _imported_modules(path)
            assert not _mentions(names, *MANAGER_ROOTS), path
            assert not _mentions(names, *CLIENT_ROOTS), path
            for other in CHANNEL_FOLDERS:
                if other == channel:
                    continue
                assert not _mentions(names, f"agentself.backends.{other}"), (
                    f"{path} imported {other}"
                )
    names = _imported_modules(INTERNAL / "registry.py")
    assert not _mentions(names, *MANAGER_ROOTS)
    assert not _mentions(names, *CLIENT_ROOTS)


def test_contracts_and_factories_have_no_vendor_types():
    paths = [INTERNAL / "registry.py"]
    for channel in CHANNEL_FOLDERS:
        for name in ("contract.py", "factory.py"):
            path = BACKENDS / channel / name
            if path.is_file():
                paths.append(path)
    assert paths
    for path in paths:
        names = _imported_modules(path)
        assert not _mentions(names, *VENDOR_ROOTS), f"{path} imported vendor {names}"


def test_client_and_cli_do_not_import_backends_or_sdks():
    files = [PKG / "client.py", PKG / "__main__.py", *_py_files(CLI)]
    for rel in files:
        names = _imported_modules(rel)
        for pkg in RA_MODULES:
            assert not _mentions(names, pkg), f"{rel} imported {pkg}: {names}"
        assert not _mentions(names, *VENDOR_ROOTS), f"{rel} imported vendor {names}"


def test_factory_files_are_not_god_files():
    for channel in CHANNEL_FOLDERS:
        path = BACKENDS / channel / "factory.py"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) < FACTORY_GOD_FILE_LINES, f"{path} grew to {len(lines)} lines"


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


def test_every_shipped_backend_is_a_folder():
    found = {
        path.name
        for path in BACKENDS.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert found == CHANNEL_FOLDERS, found
    assert {
        path.name
        for path in (BACKENDS / "wallet").iterdir()
        if path.is_dir() and path.name != "__pycache__"
    } == WALLET_BACKEND_FOLDERS
    assert {
        path.name
        for path in (BACKENDS / "email").iterdir()
        if path.is_dir() and path.name != "__pycache__"
    } == EMAIL_BACKEND_FOLDERS
    assert {
        path.name
        for path in (BACKENDS / "store").iterdir()
        if path.is_dir() and path.name != "__pycache__"
    } == STORE_BACKEND_FOLDERS
    assert not (BACKENDS / "wallet" / "cloudflare").exists()
    contract = (BACKENDS / "wallet" / "contract.py").read_text(encoding="utf-8")
    assert "key_hex" not in contract
    assert "NoEthForGas" not in contract


def test_internal_is_helpers_not_access_packages():
    found = {
        path.name
        for path in INTERNAL.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    extra = found - ALLOWED_INTERNAL
    assert extra == set(), extra
    assert (INTERNAL / "registry.py").is_file()
    assert (INTERNAL / "files.py").is_file()
    assert (INTERNAL / "host_tools.py").is_file()
    assert not (INTERNAL / "registry.py").with_name("factory.py").exists()


def test_compose_may_wire_factories_cli_may_not():
    names = _imported_modules(PKG / "compose.py")
    assert _mentions(names, "agentself.backends.wallet.factory")
    assert _mentions(names, "agentself.internal.custody")
    for path in (PKG / "__main__.py", *_py_files(CLI)):
        imported = _imported_modules(path)
        assert not _mentions(imported, "agentself.backends"), path


def test_package_lives_at_repo_root_not_src():
    assert (PROJECT_ROOT / "agentself" / "__init__.py").is_file()
    assert not (PROJECT_ROOT / "src" / "agentself").exists()


def test_main_is_a_thin_entrypoint():
    lines = [
        line
        for line in (PKG / "__main__.py").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) < 12
    assert (CLI / "app.py").is_file()
    assert (CLI / "parser.py").is_file()
    body = (PKG / "__main__.py").read_text(encoding="utf-8")
    assert "from agentself.cli.app import main, run" in body

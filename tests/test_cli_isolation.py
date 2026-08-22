"""Public CLI must stay provider-neutral and backend-free."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.support import PROJECT_ROOT, cli_env, run_cli

_CLI_ROOT = PROJECT_ROOT / "agentself" / "cli"
_FORBIDDEN_HELP = (
    "agentmail",
    "imap",
    "smtp",
    "otp",
    "oauth",
    "inbox.lv",
    "gmail",
)


def _python_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if path.is_file()]


def test_cli_does_not_import_backends() -> None:
    for path in _python_files(_CLI_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("agentself.backends"), path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("agentself.backends"), path


@pytest.mark.parametrize(
    "args",
    [(), ("email",), ("email", "connect"), ("email", "send"), ("wallet", "verify")],
)
def test_generic_help_has_no_provider_workflow_terms(
    args: tuple[str, ...], tmp_path: Path
) -> None:
    env = cli_env(tmp_path / "vault")
    proc = run_cli([*args, "-h"], env)
    assert proc.returncode == 0
    blob = proc.stdout.lower()
    for term in _FORBIDDEN_HELP:
        assert term not in blob, term

"""Public CLI must stay provider-neutral and backend-free."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import cli_env, run_cli

_FORBIDDEN_HELP = (
    "agentmail",
    "imap",
    "smtp",
    "otp",
    "oauth",
    "inbox.lv",
    "gmail",
)


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

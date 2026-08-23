from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from agentself.backends.store.contract import StoreResourceError
from agentself.internal.files import run_resolved


def run_cmd(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    stdin: bytes | None = None,
    timeout: float = 30,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return run_resolved(argv, env=env, stdin=stdin, timeout=timeout)
    except FileNotFoundError:
        tool = Path(str(argv[0])).name if argv else "tool"
        raise StoreResourceError(f"{tool} not on PATH") from None
    except subprocess.TimeoutExpired:
        raise StoreResourceError("store timeout") from None

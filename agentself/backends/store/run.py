from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from agentself.backends.store.contract import StoreResourceError
from agentself.internal.files import resolve_tool


def run_cmd(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    stdin: bytes | None = None,
    timeout: float = 30,
) -> subprocess.CompletedProcess[bytes]:
    cmd = list(argv)
    if cmd:
        cmd[0] = resolve_tool(str(cmd[0]))
    env_map = None if env is None else dict(env)
    if os.name == "nt":
        if env_map is None:
            env_map = os.environ.copy()
        env_map.setdefault("NoDefaultCurrentDirectoryInExePath", "1")
    try:
        return subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            env=env_map,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        tool = Path(str(argv[0])).name if argv else "tool"
        raise StoreResourceError(f"{tool} not on PATH") from None
    except subprocess.TimeoutExpired:
        raise StoreResourceError("store timeout") from None

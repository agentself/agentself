"""Offline probe for help, discovery, and identity commands.

Creates a disposable identity. Does not read the default identity directory,
send funds, send mail, or contact a provider.

Warm samples run in this process after imports. Fresh samples start a new
interpreter. Wall-clock numbers are informational.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SAMPLES = int(os.environ.get("AGENTSELF_PROBE_SAMPLES", "3"))
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _tool_version(name: str) -> str:
    try:
        proc = subprocess.run(
            [name, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    text = (proc.stdout or proc.stderr).strip().splitlines()
    return text[0] if text else "unavailable"


def _revision() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return proc.stdout.strip() or "unknown"


def _base_env(vault: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["AGENTSELF_IDENTITY_DIR"] = str(vault)
    env["AGENTSELF_FETCH_TOOLS"] = "0"
    env["AGENTSELF_FORBID_LIVE_AGENTMAIL"] = "1"
    src = str(ROOT)
    pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src + os.pathsep + pythonpath if pythonpath else src
    for key in (
        "AGENTSELF_IDENTITY_ID",
        "AGE_KEY_FILE",
        "AGENTSELF_EMAIL_BACKEND",
        "AGENTSELF_WALLET_BACKEND",
        "AGENTSELF_EMAIL_ADDRESS",
        "AGENTSELF_EMAIL_CREDENTIAL",
        "AGENTSELF_AGENTMAIL_API_KEY",
        "AGENTSELF_MAIL_PASSWORD",
        "AGENTSELF_ETH_RPC_URL",
    ):
        env.pop(key, None)
    return env


class _Counter:
    def __init__(self) -> None:
        self.config_reads = 0
        self.age_keygen = 0
        self.sops_decrypt = 0
        self.subprocesses = 0
        self._installed = False

    def install(self) -> None:
        if self._installed:
            return
        import agentself.internal.files as files
        import agentself.local as local

        real_run = files.subprocess.run
        real_load = local.load_json_file

        def run(argv, *args, **kwargs):
            self.subprocesses += 1
            cmd = [str(part) for part in argv]
            base = Path(cmd[0]).name.lower() if cmd else ""
            if "age-keygen" in base:
                self.age_keygen += 1
            if "sops" in base and "--decrypt" in cmd:
                self.sops_decrypt += 1
            return real_run(argv, *args, **kwargs)

        def load(path):
            if Path(path).name == "config.json":
                self.config_reads += 1
            return real_load(path)

        files.subprocess.run = run
        local.load_json_file = load
        self._installed = True

    def reset(self) -> None:
        self.config_reads = 0
        self.age_keygen = 0
        self.sops_decrypt = 0
        self.subprocesses = 0

    def snapshot(self) -> dict[str, int]:
        return {
            "config_reads": self.config_reads,
            "age_keygen": self.age_keygen,
            "sops_decrypt": self.sops_decrypt,
            "subprocesses": self.subprocesses,
        }


_COUNTER = _Counter()


def _run_measured(argv: list[str]) -> dict[str, object]:
    from contextlib import redirect_stdout
    from io import StringIO

    from agentself.cli.app import main

    _COUNTER.install()
    _COUNTER.reset()
    buf = StringIO()
    started = time.perf_counter()
    with redirect_stdout(buf):
        code = main(argv)
    elapsed_ms = (time.perf_counter() - started) * 1000
    text = buf.getvalue()
    row = _COUNTER.snapshot()
    row["exit"] = int(code)
    row["stdout_bytes"] = len(text.encode("utf-8"))
    row["elapsed_ms"] = round(elapsed_ms, 2)
    return row


def _child() -> int:
    argv = sys.argv[2:]
    print(json.dumps(_run_measured(argv)))
    return 0


def _fresh(vault: Path, argv: list[str]) -> dict[str, object]:
    env = _base_env(vault)
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--child", *argv],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    line = proc.stdout.strip().splitlines()[-1]
    return json.loads(line)


def _init(vault: Path, statement: Path) -> None:
    env = _base_env(vault)
    proc = subprocess.run(
        [sys.executable, "-m", "agentself", "init"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout + proc.stderr)
    statement.write_bytes(b"prove custody\n")


def _summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    elapsed = [float(row["elapsed_ms"]) for row in rows]
    last = rows[-1]
    return {
        "samples": len(rows),
        "elapsed_ms": elapsed,
        "elapsed_min_ms": round(min(elapsed), 2),
        "elapsed_max_ms": round(max(elapsed), 2),
        "config_reads": last["config_reads"],
        "age_keygen": last["age_keygen"],
        "sops_decrypt": last["sops_decrypt"],
        "subprocesses": last["subprocesses"],
        "stdout_bytes": last["stdout_bytes"],
        "exit": last["exit"],
    }


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        return _child()
    scenarios: dict[str, list[str]] = {
        "help": ["--help"],
        "version": ["--version"],
        "commands": ["commands"],
        "commands_wallet_authorize": ["commands", "wallet", "authorize"],
        "secret_list": ["secret", "list"],
        "wallet_address": ["wallet", "address"],
        "wallet_authorize_file": ["wallet", "authorize", "--file", "STATEMENT"],
        "wallet_authorize_positional": ["wallet", "authorize", "prove custody"],
    }
    with tempfile.TemporaryDirectory(prefix="agentself-probe-") as raw:
        vault = Path(raw) / "vault"
        statement = Path(raw) / "statement.txt"
        _init(vault, statement)
        warm_env = _base_env(vault)
        for key in list(os.environ):
            if key not in warm_env:
                os.environ.pop(key, None)
        os.environ.update(warm_env)
        # Import once so warm samples exclude interpreter startup.
        import agentself.cli.app  # noqa: F401

        report: dict[str, object] = {
            "revision": _revision(),
            "python": sys.version.split()[0],
            "os": platform.platform(),
            "age": _tool_version("age"),
            "sops": _tool_version("sops"),
            "samples": SAMPLES,
            "warm_in_process": {},
            "fresh_process": {},
        }
        warm: dict[str, object] = {}
        fresh: dict[str, object] = {}
        for name, argv in scenarios.items():
            if "STATEMENT" in argv:
                argv = [
                    statement.as_posix() if part == "STATEMENT" else part
                    for part in argv
                ]
            if name in {"help", "version", "commands", "commands_wallet_authorize"}:
                pass
            warm_rows = [_run_measured(argv) for _ in range(SAMPLES)]
            fresh_rows = [_fresh(vault, argv) for _ in range(SAMPLES)]
            warm[name] = _summarize(warm_rows)
            fresh[name] = _summarize(fresh_rows)
        report["warm_in_process"] = warm
        report["fresh_process"] = fresh
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

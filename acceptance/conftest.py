from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    @property
    def payload(self) -> dict[str, object]:
        assert self.stderr == "", self.details
        try:
            value = json.loads(self.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(self.details) from exc
        assert isinstance(value, dict), self.details
        return value

    @property
    def details(self) -> str:
        return (
            f"command: {' '.join(self.command)}\n"
            f"exit: {self.returncode}\n"
            f"stdout:\n{self.stdout}\n"
            f"stderr:\n{self.stderr}"
        )

    def expect(self, code: int) -> CommandResult:
        assert self.returncode == code, self.details
        return self


class Cli:
    def __init__(self, command: tuple[str, ...], root: Path) -> None:
        self._command = command
        self.root = root
        self.identity_dir = root / "identity"
        self.work_dir = root / "work"
        self.tools_dir = root / "tools"

    def run(
        self,
        *args: str,
        identity_dir: Path | None = None,
        cwd: Path | None = None,
        input: str | None = None,
    ) -> CommandResult:
        identity = identity_dir or self.identity_dir
        working = cwd or self.work_dir
        identity.parent.mkdir(parents=True, exist_ok=True)
        working.mkdir(parents=True, exist_ok=True)
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        command = (*self._command, *args)
        process = subprocess.run(
            command,
            cwd=working,
            env=self._environment(identity),
            input=input,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return CommandResult(
            command=command,
            returncode=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
        )

    def json(
        self,
        *args: str,
        expected_code: int = 0,
        identity_dir: Path | None = None,
        cwd: Path | None = None,
        input: str | None = None,
    ) -> CommandResult:
        result = self.run(
            "--json",
            *args,
            identity_dir=identity_dir,
            cwd=cwd,
            input=input,
        ).expect(expected_code)
        result.payload
        return result

    def snapshot(self, path: Path | None = None) -> dict[str, bytes]:
        root = path or self.identity_dir
        if not root.exists():
            return {}
        return {
            item.relative_to(root).as_posix(): item.read_bytes()
            for item in root.rglob("*")
            if item.is_file()
        }

    def _environment(self, identity_dir: Path) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        for key in tuple(env):
            if key.startswith(("AGENTSELF_", "SOPS_AGE_")) or key in {
                "AGE_KEY_FILE",
                "AGE_SECRET_KEY",
            }:
                env.pop(key, None)
        env["AGENTSELF_IDENTITY_DIR"] = str(identity_dir)
        env["AGENTSELF_TOOLS"] = str(self.tools_dir)
        env["AGENTSELF_FETCH_TOOLS"] = "0"
        env["AGENTSELF_FORBID_LIVE_AGENTMAIL"] = "1"
        return env


@pytest.fixture
def cli(tmp_path: Path) -> Cli:
    executable = os.environ.get("AGENTSELF_ACCEPTANCE_EXE", "").strip()
    command = (executable,) if executable else (sys.executable, "-m", "agentself")
    return Cli(command, tmp_path)

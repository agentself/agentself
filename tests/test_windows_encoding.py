"""Windows-facing encoding, BOM, CRLF, SHA-256, and JSON round trips."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentself.internal.text import strip_one_trailing_newline

from tests.support import cli_env, run_cli, value_file


def test_strip_one_trailing_newline_accepts_cr_lf_and_crlf() -> None:
    assert strip_one_trailing_newline("am_key\r\n") == "am_key"
    assert strip_one_trailing_newline("am_key\n") == "am_key"
    assert strip_one_trailing_newline("am_key\r") == "am_key"
    assert strip_one_trailing_newline("am_key") == "am_key"
    assert strip_one_trailing_newline("tok\r\nAuthorization: Bearer x") == (
        "tok\r\nAuthorization: Bearer x"
    )
    assert strip_one_trailing_newline("am_key\x00\r") == "am_key\x00"


def test_secret_file_round_trip_preserves_exact_utf8_bytes(
    tmp_path: Path,
) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0
    payload = "café token\r\n"
    source = tmp_path / "in.txt"
    source.write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))
    created = run_cli(
        ["--json", "secret", "create", "demo.token", "--file", str(source)], env
    )
    assert created.returncode == 0, created.stderr
    dest = tmp_path / "out.txt"
    got = json.loads(
        run_cli(
            ["--json", "secret", "get", "demo.token", "--file", str(dest)], env
        ).stdout
    )
    assert got["bytes"] == len(source.read_bytes())
    assert dest.read_bytes() == source.read_bytes()
    meta = json.loads(
        run_cli(["--json", "secret", "get", "demo.token", "--meta"], env).stdout
    )
    assert len(meta["sha256"]) == 64
    assert "value" not in meta


def test_json_unicode_round_trip(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0
    created = run_cli(
        [
            "--json",
            "secret",
            "create",
            "demo.token",
            "--file",
            value_file(tmp_path, "café"),
        ],
        env,
    )
    assert created.returncode == 0, created.stderr
    assert json.loads(created.stdout)["ok"] is True
    got = json.loads(
        run_cli(["--json", "secret", "get", "demo.token", "--print"], env).stdout
    )
    assert got["value"] == "café"


@pytest.mark.skipif(
    sys.platform != "win32", reason="PowerShell coverage is Windows-only"
)
def test_powershell_convertfrom_json_unicode_and_exit_codes(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    init = run_cli(["--json", "init"], env)
    assert init.returncode == 0, init.stderr
    blob = tmp_path / "init.json"
    blob.write_text(init.stdout, encoding="utf-8")
    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell
    script = (
        f"$ErrorActionPreference = 'Stop'; "
        f"$obj = Get-Content -Raw -Encoding utf8 {str(blob)!s} | ConvertFrom-Json; "
        f"if (-not $obj.ok) {{ exit 1 }}; "
        f"Write-Output $obj.address"
    )
    converted = subprocess.run(
        [shell, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert converted.returncode == 0, converted.stdout + converted.stderr
    assert converted.stdout.strip().startswith("0x")
    missing = run_cli(["--json", "secret", "exists", "missing.token"], env)
    assert missing.returncode == 3
    assert missing.stderr == ""
    assert json.loads(missing.stdout)["ok"] is False

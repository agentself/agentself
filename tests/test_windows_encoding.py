"""Windows-facing encoding, BOM, CRLF, SHA-256, and JSON round trips."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentself.cli.app import _write_raw
from agentself.internal.eoa import parse_secp256k1_hex
from agentself.internal.text import strip_one_trailing_newline

from tests.support import PROJECT_ROOT, cli_env, run_cli, value_file


def test_parse_secp256k1_hex_strips_bom_and_whitespace() -> None:
    body = "ab" * 32
    assert parse_secp256k1_hex("0x" + body) == "0x" + body
    assert parse_secp256k1_hex("\ufeff0x" + body + "\r\n") == "0x" + body
    assert parse_secp256k1_hex("0X" + body.upper()) == "0x" + body.upper()
    assert parse_secp256k1_hex(body) == "0x" + body
    assert parse_secp256k1_hex("not-a-key") is None
    assert parse_secp256k1_hex("\ufeffnot-a-key") is None
    assert parse_secp256k1_hex("0x" + "ab" * 31) is None


def test_strip_one_trailing_newline_accepts_cr_lf_and_crlf() -> None:
    assert strip_one_trailing_newline("am_key\r\n") == "am_key"
    assert strip_one_trailing_newline("am_key\n") == "am_key"
    assert strip_one_trailing_newline("am_key\r") == "am_key"
    assert strip_one_trailing_newline("am_key") == "am_key"
    assert strip_one_trailing_newline("tok\r\nAuthorization: Bearer x") == (
        "tok\r\nAuthorization: Bearer x"
    )
    assert strip_one_trailing_newline("am_key\x00\r") == "am_key\x00"


def test_secret_file_round_trip_strips_bom_and_keeps_trailing_newline(
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
    stored = payload.encode("utf-8")
    assert got["bytes"] == len(stored)
    assert dest.read_bytes() == stored
    meta = json.loads(
        run_cli(["--json", "secret", "get", "demo.token", "--meta"], env).stdout
    )
    assert len(meta["sha256"]) == 64
    assert "value" not in meta


def test_init_refuses_windows_reserved_identity_names(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    for name in ("CON", "nul", "COM1", "agent."):
        proc = run_cli(["--json", "init", "--id", name], env)
        assert proc.returncode == 2, (name, proc.stdout + proc.stderr)
        data = json.loads(proc.stdout)
        assert data["ok"] is False
        assert data["error"] == "refused"


def test_secret_create_refuses_windows_reserved_names(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0
    proc = run_cli(
        ["--json", "secret", "create", "NUL", "--file", value_file(tmp_path, "x")],
        env,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["error"] == "refused"


def _run_bytes(
    args: list[str], env: dict[str, str]
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "agentself", *args],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        timeout=60,
    )


def test_raw_get_writes_exact_stored_bytes(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0
    no_nl = tmp_path / "no-nl.txt"
    no_nl.write_bytes(b"plain-secret")
    created = run_cli(
        ["--json", "secret", "create", "demo.nonewline", "--file", str(no_nl)],
        env,
    )
    assert created.returncode == 0, created.stderr
    crlf = tmp_path / "crlf.txt"
    crlf.write_bytes(b"crlf-secret\r\n")
    created_crlf = run_cli(
        ["--json", "secret", "create", "demo.crlf", "--file", str(crlf)],
        env,
    )
    assert created_crlf.returncode == 0, created_crlf.stderr

    printed = _run_bytes(["secret", "get", "demo.nonewline", "--raw"], env)
    assert printed.returncode == 0, printed.stderr
    assert printed.stdout == b"plain-secret"
    assert not printed.stdout.endswith(b"\x0a")

    printed_crlf = _run_bytes(["secret", "get", "demo.crlf", "--raw"], env)
    assert printed_crlf.returncode == 0, printed_crlf.stderr
    assert printed_crlf.stdout == b"crlf-secret\r\n"

    dest = tmp_path / "out-crlf.txt"
    got = run_cli(["--json", "secret", "get", "demo.crlf", "--file", str(dest)], env)
    assert got.returncode == 0, got.stderr
    assert dest.read_bytes() == b"crlf-secret\r\n"

    note_src = tmp_path / "note.txt"
    note_src.write_bytes(b"note-body")
    noted = run_cli(["--json", "note", "set", "handoff", "--file", str(note_src)], env)
    assert noted.returncode == 0, noted.stderr
    note_json = _run_bytes(["note", "get", "handoff"], env)
    assert note_json.returncode == 0, note_json.stderr
    assert note_json.stdout.endswith(b"\x0a")
    assert json.loads(note_json.stdout.decode("utf-8"))["value"] == "note-body"
    note_raw = _run_bytes(["note", "get", "handoff", "--raw"], env)
    assert note_raw.returncode == 0, note_raw.stderr
    assert note_raw.stdout == b"note-body"


def test_write_raw_without_buffer_and_flush_error(monkeypatch) -> None:
    class TextOnly:
        def __init__(self) -> None:
            self.written: list[str] = []

        def write(self, text: str) -> int:
            self.written.append(text)
            return len(text)

    text_only = TextOnly()
    monkeypatch.setattr(sys, "stdout", text_only)
    _write_raw("plain")
    assert text_only.written == ["plain"]

    class FlushFails:
        def __init__(self) -> None:
            self.written: list[bytes] = []
            self.buffer = self

        def write(self, data: bytes) -> int:
            self.written.append(data)
            return len(data)

        def flush(self) -> None:
            raise OSError("flush failed")

    flaky = FlushFails()
    monkeypatch.setattr(sys, "stdout", flaky)
    _write_raw("plain")
    assert flaky.written == [b"plain"]


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
    got = json.loads(run_cli(["--json", "secret", "get", "demo.token"], env).stdout)
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

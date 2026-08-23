"""Pinned age/sops fetch lands in the host tools dir, never the vault."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import zipfile

import pytest

from agentself.internal import host_tools
from agentself.internal.host_tools import HostToolError, ensure_host_tools

from tests.support import cli_env, run_cli


def _zip_age() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("age/age.exe", b"fake-age-windows")
        archive.writestr("age/age-keygen.exe", b"fake-age-keygen-windows")
    return buf.getvalue()


def _tar_age() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for name, body in (
            ("age/age", b"fake-age-unix"),
            ("age/age-keygen", b"fake-age-keygen-unix"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def _digest_map(
    age_name: str, age_blob: bytes, sops_name: str, sops_blob: bytes
) -> dict[str, str]:
    data = dict(host_tools._DIGESTS)
    data[age_name] = hashlib.sha256(age_blob).hexdigest()
    data[sops_name] = hashlib.sha256(sops_blob).hexdigest()
    return data


def test_tools_dir_default_is_not_identity_dir(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("AGENTSELF_IDENTITY_DIR", str(vault))
    monkeypatch.delenv("AGENTSELF_TOOLS", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    dest = host_tools.tools_dir()
    assert (
        dest == tmp_path / "local" / "agentself" / "bin"
        or dest == tmp_path / "share" / "agentself" / "bin"
    )
    assert vault not in dest.parents or dest != vault
    assert dest != vault
    assert "vault" not in dest.parts


def test_fetch_disabled_does_not_download(tmp_path, monkeypatch):
    dest = tmp_path / "tools"
    dest.mkdir()
    monkeypatch.setenv("AGENTSELF_TOOLS", str(dest))
    monkeypatch.setenv("AGENTSELF_FETCH_TOOLS", "0")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    called = []

    def boom(url: str) -> bytes:
        called.append(url)
        raise AssertionError("must not fetch")

    monkeypatch.setattr(host_tools, "_http_get", boom)
    ensure_host_tools()
    assert called == []
    assert list(dest.iterdir()) == []


def test_fetch_installs_windows_bins(tmp_path, monkeypatch):
    dest = tmp_path / "tools"
    age_blob = _zip_age()
    sops_blob = b"fake-sops-windows"
    monkeypatch.setenv("AGENTSELF_TOOLS", str(dest))
    monkeypatch.setenv("AGENTSELF_FETCH_TOOLS", "1")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    monkeypatch.setattr(host_tools, "_host_kind", lambda: ("windows", "amd64"))
    monkeypatch.setattr(
        host_tools,
        "_DIGESTS",
        _digest_map(
            "age-v1.3.1-windows-amd64.zip",
            age_blob,
            "sops-v3.13.3.amd64.exe",
            sops_blob,
        ),
    )

    def get(url: str) -> bytes:
        if url.endswith(".zip"):
            return age_blob
        if url.endswith(".exe"):
            return sops_blob
        raise AssertionError(url)

    monkeypatch.setattr(host_tools, "_http_get", get)
    ensure_host_tools()
    assert (dest / "age.exe").read_bytes() == b"fake-age-windows"
    assert (dest / "age-keygen.exe").read_bytes() == b"fake-age-keygen-windows"
    assert (dest / "sops.exe").read_bytes() == sops_blob
    assert not (dest / ".age-extract").exists()
    assert os.environ["PATH"].split(os.pathsep)[0] == str(dest)


def test_fetch_installs_unix_bins(tmp_path, monkeypatch):
    dest = tmp_path / "tools"
    age_blob = _tar_age()
    sops_blob = b"fake-sops-unix"
    monkeypatch.setenv("AGENTSELF_TOOLS", str(dest))
    monkeypatch.setenv("AGENTSELF_FETCH_TOOLS", "1")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    monkeypatch.setattr(host_tools, "_host_kind", lambda: ("linux", "amd64"))
    monkeypatch.setattr(
        host_tools,
        "_DIGESTS",
        _digest_map(
            "age-v1.3.1-linux-amd64.tar.gz",
            age_blob,
            "sops-v3.13.3.linux.amd64",
            sops_blob,
        ),
    )

    def get(url: str) -> bytes:
        if url.endswith(".tar.gz"):
            return age_blob
        return sops_blob

    monkeypatch.setattr(host_tools, "_http_get", get)
    ensure_host_tools()
    assert (dest / "age").read_bytes() == b"fake-age-unix"
    assert (dest / "age-keygen").read_bytes() == b"fake-age-keygen-unix"
    assert (dest / "sops").read_bytes() == sops_blob


def test_checksum_mismatch_fails_closed(tmp_path, monkeypatch):
    dest = tmp_path / "tools"
    monkeypatch.setenv("AGENTSELF_TOOLS", str(dest))
    monkeypatch.setenv("AGENTSELF_FETCH_TOOLS", "1")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    monkeypatch.setattr(host_tools, "_host_kind", lambda: ("linux", "amd64"))
    monkeypatch.setattr(host_tools, "_http_get", lambda url: b"tampered")
    with pytest.raises(HostToolError, match="checksum mismatch"):
        ensure_host_tools()
    assert not (dest / "sops").exists()
    assert not (dest / "age").exists()


def test_doctor_fetch_error_is_json_object(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    empty = tmp_path / "empty"
    empty.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    monkeypatch.setenv("AGENTSELF_IDENTITY_DIR", str(vault))
    monkeypatch.setenv("AGENTSELF_TOOLS", str(tools))
    monkeypatch.setenv("AGENTSELF_FETCH_TOOLS", "1")
    monkeypatch.setenv("PATH", str(empty))

    def boom(url: str) -> bytes:
        raise HostToolError("could not fetch host tools")

    monkeypatch.setattr(host_tools, "_http_get", boom)
    from agentself.cli.app import main

    code = main(["--json", "diagnose"])
    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert captured.err == ""
    data = json.loads(captured.out)
    assert data["ok"] is False
    assert data["error"] == "error"
    assert data["reason"] == "age not on PATH"
    assert data["next"] == "agentself install --tools"
    assert captured.out.count("\n") == 1

    tools_code = main(["--json", "install", "--tools"])
    tools_cap = capsys.readouterr()
    assert tools_code == 1, tools_cap.out + tools_cap.err
    assert tools_cap.err == ""
    tools_data = json.loads(tools_cap.out)
    assert tools_data == {
        "ok": False,
        "error": "error",
        "reason": "could not fetch host tools",
        "next": "agentself install --tools",
    }


def test_show_backends_and_version_do_not_fetch(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    monkeypatch.setenv("AGENTSELF_IDENTITY_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("AGENTSELF_TOOLS", str(tools))
    monkeypatch.setenv("AGENTSELF_FETCH_TOOLS", "1")
    monkeypatch.setenv("PATH", str(empty))
    called: list[str] = []

    def boom(url: str) -> bytes:
        called.append(url)
        raise AssertionError("must not fetch")

    monkeypatch.setattr(host_tools, "_http_get", boom)
    from agentself.cli.app import main

    assert main(["--version"]) == 0
    assert main(["backends"]) == 0
    assert main(["show"]) == 2
    assert called == []


def test_cli_doctor_fetch_off_keeps_age_missing_shape(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    env["PATH"] = str(empty)
    proc = run_cli(["diagnose"], env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert proc.stderr == "error: age not on PATH\nnext: agentself install --tools\n"
    js = run_cli(["--json", "diagnose"], env)
    assert js.returncode == 1
    data = json.loads(js.stdout or js.stderr)
    assert data["reason"] == "age not on PATH"
    assert data["next"] == "agentself install --tools"
    init = run_cli(["init"], env)
    assert init.returncode == 1
    assert "install --tools" in init.stderr

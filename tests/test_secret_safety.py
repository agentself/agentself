"""Protected wallet.key, --meta without values, and reserved secret names."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentself.internal.custody.errors import ProtectedName, Refused

from tests.support import cli_env, init_identity, run_cli, value_file


def _init(tmp_path: Path) -> dict[str, str]:
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["--json", "init"], env)
    assert proc.returncode == 0, proc.stderr
    return env


def test_wallet_key_is_protected_and_requires_unsafe(tmp_path: Path) -> None:
    env = _init(tmp_path)
    listed = json.loads(run_cli(["--json", "secret", "list"], env).stdout)
    assert "wallet.key" in listed["names"]
    assert "wallet.key" in listed["protected"]
    refused = run_cli(["--json", "secret", "get", "wallet.key"], env)
    assert refused.returncode == 2
    payload = json.loads(refused.stdout)
    assert payload["error"] == "refused"
    assert "protected" in payload["reason"]
    exported = run_cli(["--json", "secret", "get", "wallet.key", "--unsafe"], env)
    assert exported.returncode == 0
    hexkey = json.loads(exported.stdout)["value"]
    assert hexkey.startswith("0x")
    dest = tmp_path / "wallet.key"
    wrote = run_cli(
        ["secret", "get", "wallet.key", "--unsafe", "--file", str(dest)], env
    )
    assert wrote.returncode == 0, wrote.stderr
    assert dest.read_text(encoding="utf-8").startswith("0x")
    if os.name != "nt":
        assert dest.stat().st_mode & 0o777 == 0o600
    new_key = "0x" + "ab" * 32
    updated = run_cli(
        [
            "--json",
            "secret",
            "update",
            "wallet.key",
            "--file",
            value_file(tmp_path, new_key, "new-key.txt"),
        ],
        env,
    )
    assert updated.returncode == 2, updated.stdout + updated.stderr
    payload = json.loads(updated.stdout)
    assert payload["error"] == "refused"
    assert "protected" in payload["reason"]
    still = json.loads(
        run_cli(["--json", "secret", "get", "wallet.key", "--unsafe"], env).stdout
    )["value"]
    assert still == hexkey
    forced = run_cli(
        [
            "--json",
            "secret",
            "update",
            "wallet.key",
            "--unsafe",
            "--file",
            value_file(tmp_path, new_key, "forced-key.txt"),
        ],
        env,
    )
    assert forced.returncode == 0, forced.stdout + forced.stderr
    replaced = json.loads(
        run_cli(["--json", "secret", "get", "wallet.key", "--unsafe"], env).stdout
    )["value"]
    assert replaced == new_key


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_secret_export_file_is_private_and_does_not_chmod_parent(
    tmp_path: Path,
) -> None:
    env = _init(tmp_path)
    parent = tmp_path / "export-dir"
    parent.mkdir(mode=0o755)
    dest = parent / "wallet.key"
    wrote = run_cli(
        ["secret", "get", "wallet.key", "--unsafe", "--file", str(dest)], env
    )
    assert wrote.returncode == 0, wrote.stderr
    assert dest.stat().st_mode & 0o777 == 0o600
    assert parent.stat().st_mode & 0o777 == 0o755


def test_secret_meta_omits_value(tmp_path: Path) -> None:
    env = _init(tmp_path)
    created = run_cli(
        [
            "--json",
            "secret",
            "create",
            "demo.token",
            "--file",
            value_file(tmp_path, "alpha"),
        ],
        env,
    )
    assert created.returncode == 0, created.stderr
    meta = json.loads(
        run_cli(["--json", "secret", "get", "demo.token", "--meta"], env).stdout
    )
    assert "value" not in meta
    assert meta["bytes"] == 5
    assert len(meta["sha256"]) == 64


def test_secret_plaintext_json_includes_value(tmp_path: Path) -> None:
    env = _init(tmp_path)
    assert run_cli(["secret", "create", "demo.token", "alpha"], env).returncode == 0
    got = run_cli(["--json", "secret", "get", "demo.token"], env)
    assert got.returncode == 0
    assert json.loads(got.stdout)["value"] == "alpha"
    raw = run_cli(["secret", "get", "demo.token", "--raw"], env)
    assert raw.returncode == 0
    assert raw.stdout == "alpha"


def test_reserved_secret_names_are_hidden(tmp_path: Path) -> None:
    env = _init(tmp_path)
    proc = run_cli(
        [
            "--json",
            "secret",
            "create",
            "internal.setup.demo",
            "--file",
            value_file(tmp_path, "x"),
        ],
        env,
    )
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["error"] == "refused"
    listed = json.loads(run_cli(["--json", "secret", "list"], env).stdout)
    assert all(not item.startswith("internal.") for item in listed["names"])


def test_client_update_wallet_key_requires_unsafe(app, monkeypatch) -> None:
    init_identity(app, monkeypatch)
    app.client.wallet_address()
    current = app.client.get("wallet.key")
    replacement = "0x" + "ab" * 32
    with pytest.raises(ProtectedName):
        app.client.update("wallet.key", replacement)
    assert app.client.get("wallet.key") == current
    store_updates = [
        call
        for call in app.stores.calls
        if call[0] == "update" and call[2] == "wallet.key"
    ]
    assert store_updates == []
    app.client.update("wallet.key", replacement, unsafe=True)
    assert app.client.get("wallet.key") == replacement


def test_client_update_wallet_key_refuses_non_hex(app, monkeypatch) -> None:
    init_identity(app, monkeypatch)
    app.client.wallet_address()
    current = app.client.get("wallet.key")
    with pytest.raises(Refused, match="wallet.key is not a key"):
        app.client.update("wallet.key", "not-a-key", unsafe=True)
    with pytest.raises(Refused, match="wallet.key is not a key"):
        app.client.update("wallet.key", "\ufeffnot-a-key\n", unsafe=True)
    assert app.client.get("wallet.key") == current


def test_client_update_wallet_key_normalizes_bom(app, monkeypatch) -> None:
    init_identity(app, monkeypatch)
    app.client.wallet_address()
    replacement = "0x" + "cd" * 32
    app.client.update("wallet.key", "\ufeff" + replacement + "\r\n", unsafe=True)
    assert app.client.get("wallet.key") == replacement


def test_secret_file_wallet_key_strips_bom_and_refuses_garbage(
    tmp_path: Path,
) -> None:
    env = _init(tmp_path)
    original = json.loads(
        run_cli(["--json", "secret", "get", "wallet.key", "--unsafe"], env).stdout
    )["value"]
    replacement = "0x" + "ab" * 32
    source = tmp_path / "new-key.txt"
    source.write_bytes(b"\xef\xbb\xbf" + replacement.encode("utf-8") + b"\r\n")
    forced = run_cli(
        [
            "--json",
            "secret",
            "update",
            "wallet.key",
            "--unsafe",
            "--file",
            str(source),
        ],
        env,
    )
    assert forced.returncode == 0, forced.stdout + forced.stderr
    stored = json.loads(
        run_cli(["--json", "secret", "get", "wallet.key", "--unsafe"], env).stdout
    )["value"]
    assert stored == replacement
    bad = tmp_path / "bad-key.txt"
    bad.write_bytes(b"\xef\xbb\xbfnot-a-key\n")
    refused = run_cli(
        [
            "--json",
            "secret",
            "update",
            "wallet.key",
            "--unsafe",
            "--file",
            str(bad),
        ],
        env,
    )
    assert refused.returncode == 2, refused.stdout + refused.stderr
    payload = json.loads(refused.stdout)
    assert payload["error"] == "refused"
    assert payload["reason"] == "wallet.key is not a key"
    still = json.loads(
        run_cli(["--json", "secret", "get", "wallet.key", "--unsafe"], env).stdout
    )["value"]
    assert still == replacement
    assert still != original

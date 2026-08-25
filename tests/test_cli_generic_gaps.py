"""Generic CLI gaps: bind an existing wallet key, bulk secrets, compact catalogs."""

from __future__ import annotations

import json
from pathlib import Path

from eth_account import Account

from agentself.internal.eoa import generate_secp256k1

from tests.support import PROJECT_ROOT, cli_env, run_cli, value_file


def _blob(proc) -> str:
    return proc.stdout + proc.stderr


def test_init_wallet_key_file_binds_existing_key(tmp_path: Path) -> None:
    key = generate_secp256k1()
    key_path = value_file(tmp_path, key + "\n", "existing.key")
    env = {**cli_env(tmp_path / "vault"), "AGENTSELF_LOG": "1"}
    proc = run_cli(["--json", "init", "--wallet-key-file", key_path], env)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["ok"] is True
    assert data["address"] == Account.from_key(key).address
    blob = _blob(proc)
    assert key not in blob
    assert key[2:] not in blob
    shown = run_cli(["--json", "wallet", "address"], env)
    assert json.loads(shown.stdout)["address"] == data["address"]
    exported = run_cli(["--json", "secret", "get", "wallet.key", "--unsafe"], env)
    assert exported.returncode == 0
    assert json.loads(exported.stdout)["value"] == key


def test_init_wallet_key_file_stdin_and_bad_key(tmp_path: Path) -> None:
    key = generate_secp256k1()
    env = cli_env(tmp_path / "a")
    piped = run_cli(["--json", "init", "--wallet-key-file", "-"], env, input=key + "\n")
    assert piped.returncode == 0, piped.stderr
    assert json.loads(piped.stdout)["address"] == Account.from_key(key).address
    assert key not in _blob(piped)

    empty_env = cli_env(tmp_path / "b")
    empty = run_cli(
        ["--json", "init", "--wallet-key-file", value_file(tmp_path, "", "empty.key")],
        empty_env,
    )
    assert empty.returncode == 2
    payload = json.loads(empty.stdout)
    assert payload["error"] == "refused"
    assert payload["reason"] == "no_key"

    bad_env = cli_env(tmp_path / "c")
    bad_text = "not-a-hex-key"
    bad = run_cli(
        [
            "--json",
            "init",
            "--wallet-key-file",
            value_file(tmp_path, bad_text, "bad.key"),
        ],
        bad_env,
    )
    assert bad.returncode == 2
    refused = json.loads(bad.stdout)
    assert refused["error"] == "refused"
    assert refused["reason"] == "no_key"
    assert bad_text not in _blob(bad)


def test_init_force_does_not_replace_wallet_key_without_unsafe(tmp_path: Path) -> None:
    first_key = generate_secp256k1()
    second_key = generate_secp256k1()
    assert first_key != second_key
    env = cli_env(tmp_path / "vault")
    started = run_cli(
        [
            "--json",
            "init",
            "--wallet-key-file",
            value_file(tmp_path, first_key, "first.key"),
        ],
        env,
    )
    assert started.returncode == 0, started.stderr
    first_addr = json.loads(started.stdout)["address"]
    blocked = run_cli(
        [
            "--json",
            "init",
            "--force",
            "--wallet-key-file",
            value_file(tmp_path, second_key, "second.key"),
        ],
        env,
    )
    assert blocked.returncode == 2
    payload = json.loads(blocked.stdout)
    assert payload["error"] == "refused"
    assert "protected" in payload["reason"]
    assert second_key not in _blob(blocked)
    assert first_key not in _blob(blocked)
    shown = run_cli(["--json", "wallet", "address"], env)
    assert json.loads(shown.stdout)["address"] == first_addr
    replaced = run_cli(
        [
            "--json",
            "init",
            "--wallet-key-file",
            value_file(tmp_path, second_key, "replace.key"),
            "--unsafe",
        ],
        env,
    )
    assert replaced.returncode == 0, replaced.stderr
    assert (
        json.loads(replaced.stdout)["address"] == Account.from_key(second_key).address
    )
    assert second_key not in _blob(replaced)


def test_secret_create_from_dir_skips_wallet_key_without_unsafe(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0
    folder = tmp_path / "secrets"
    folder.mkdir()
    (folder / "api.token").write_bytes(b"token-one\n")
    (folder / "other.secret").write_bytes(b"token-two\n")
    (folder / "wallet.key").write_bytes((generate_secp256k1() + "\n").encode("ascii"))
    (folder / ".junk").write_bytes(b"hidden\n")
    nested = folder / "nested"
    nested.mkdir()
    (nested / "skip.me").write_bytes(b"nested\n")
    proc = run_cli(["--json", "secret", "create", "--from-dir", str(folder)], env)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["ok"] is True
    assert sorted(data["created"]) == ["api.token", "other.secret"]
    assert data["unchanged"] == []
    assert data["refused"] == ["wallet.key"]
    blob = json.dumps(data) + _blob(proc)
    assert "token-one" not in blob
    assert "token-two" not in blob
    listed = json.loads(run_cli(["--json", "secret", "list"], env).stdout)
    assert "api.token" in listed["names"]
    assert "other.secret" in listed["names"]
    got = run_cli(["--json", "secret", "get", "api.token"], env)
    assert json.loads(got.stdout)["value"] == "token-one\n"


def test_secret_create_from_files_reports_names_only(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    assert run_cli(["--json", "init"], env).returncode == 0
    first = value_file(tmp_path, "alpha-value", "alpha.txt")
    second = value_file(tmp_path, "beta-value", "beta.txt")
    proc = run_cli(
        [
            "--json",
            "secret",
            "create",
            "--from-files",
            f"alpha={first}",
            "--from-files",
            f"beta={second}",
        ],
        env,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["created"] == ["alpha", "beta"]
    assert data["unchanged"] == []
    assert data["refused"] == []
    assert "alpha-value" not in _blob(proc)
    again = run_cli(
        ["--json", "secret", "create", "--from-files", f"alpha={first}"],
        env,
    )
    assert again.returncode == 0
    assert json.loads(again.stdout)["unchanged"] == ["alpha"]
    clash = run_cli(
        [
            "--json",
            "secret",
            "create",
            "--from-files",
            f"alpha={value_file(tmp_path, 'other-value', 'other.txt')}",
        ],
        env,
    )
    assert clash.returncode == 2
    refused = json.loads(clash.stdout)
    assert refused["ok"] is False
    assert refused["refused"] == ["alpha"]
    assert "other-value" not in clash.stdout + clash.stderr


def test_skill_start_safely_prefers_json_version_and_diagnose() -> None:
    text = (PROJECT_ROOT / "agentself" / "skills" / "agentself" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    start, rest = text.split("## Common path", 1)
    common = rest.split("## Open the relevant workflow", 1)[0]
    body = start + common
    assert "agentself --version" in start
    assert "agentself diagnose" in start
    assert '"cli": 2' in start
    assert "agentself --json --version" not in start
    assert "agentself --json diagnose" not in start
    assert "agentself --json commands" not in start
    assert "--raw" in start
    assert "never put the message on argv" in common
    assert "agentself wallet authorize --file PATH" in common
    assert "secret list" in common
    assert "store list" not in common
    assert "agentself store" not in common
    assert "--print" not in body
    assert "authorize --print" not in body
    assert "wallet authorize MESSAGE" not in body
    assert "references/email-connect.md" in text
    assert "references/mail.md" in text
    assert "references/handoff.md" in text


def _store_list_refused(proc) -> dict:
    blob = _blob(proc)
    assert proc.returncode == 2, blob
    assert proc.stderr == ""
    data = json.loads(proc.stdout)
    assert data["ok"] is False
    assert data["error"] == "refused"
    assert data["reason"] == "store is the secret backend, not a command"
    assert data["next"] == "agentself secret list"
    assert "restore" not in blob.lower()
    assert "did you mean" not in data["reason"]
    assert "value" not in data
    assert "names" not in data
    return data


def test_unknown_store_list_points_to_secret_list(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    for args in (["store", "list"], ["--json", "store", "list"]):
        data = _store_list_refused(run_cli(args, env))
        assert data["next"] == "agentself secret list"


def test_commands_still_feature_secret_list(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["commands"], env)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    names = [item["name"] for item in data["commands"]]
    assert "secret" in names
    secret = next(item for item in data["commands"] if item["name"] == "secret")
    assert "list" in secret["args"]
    assert secret["next"] == "agentself secret list"
    assert "secret list" in names
    leaf = next(item for item in data["commands"] if item["name"] == "secret list")
    assert leaf["next"] == "agentself secret list"
    blob = json.dumps(data)
    assert "value" not in blob
    assert "AGE-SECRET-KEY" not in blob

"""Public CLI 2 friction fixes: identity selection and file-based authorize/verify."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from agentself.internal.text import sha256_text

from tests.support import PROJECT_ROOT, cli_env, run_cli


def _isolated_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "")
    src = str(PROJECT_ROOT)
    env["PYTHONPATH"] = src + os.pathsep + pythonpath if pythonpath else src
    env["AGENTSELF_FETCH_TOOLS"] = "0"
    env["AGENTSELF_FORBID_LIVE_AGENTMAIL"] = "1"
    for key in (
        "AGENTSELF_IDENTITY_DIR",
        "AGENTSELF_IDENTITY_ID",
        "AGE_KEY_FILE",
        "AGENTSELF_MAIL_DOMAIN",
        "AGENTSELF_EMAIL_BACKEND",
        "AGENTSELF_WALLET_BACKEND",
        "AGENTSELF_EMAIL_ADDRESS",
        "AGENTSELF_EMAIL_CREDENTIAL",
        "AGENTSELF_AGENTMAIL_API_KEY",
        "AGENTSELF_MAIL_PASSWORD",
    ):
        env.pop(key, None)
    return env


def _init(vault: Path, env: dict[str, str] | None = None) -> dict[str, str]:
    environment = env or cli_env(vault)
    proc = run_cli(["init"], environment)
    assert proc.returncode == 0, proc.stderr
    return environment


def test_identity_dir_flag_beats_env_before_and_after_command(tmp_path: Path) -> None:
    flagged = tmp_path / "flagged"
    env_vault = tmp_path / "from-env"
    env = cli_env(env_vault)
    assert run_cli(["init"], env).returncode == 0
    assert run_cli(["init"], cli_env(flagged)).returncode == 0
    before = run_cli(["--identity-dir", str(flagged), "show"], env)
    after = run_cli(["show", "--identity-dir", str(flagged)], env)
    equals = run_cli([f"--identity-dir={flagged}", "show"], env)
    assert before.returncode == after.returncode == equals.returncode == 0
    assert json.loads(before.stdout)["identity_dir"] == str(flagged)
    assert json.loads(after.stdout)["identity_dir"] == str(flagged)
    assert json.loads(equals.stdout)["identity_dir"] == str(flagged)
    env_shown = run_cli(["show"], env)
    assert json.loads(env_shown.stdout)["identity_dir"] == str(env_vault)
    cfg = json.loads((flagged / "config.json").read_text(encoding="utf-8"))
    assert "identity_dir" not in cfg


def test_identity_dir_flag_is_not_persisted_and_defaults_to_home(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    env = _isolated_env()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    proc = run_cli(["show"], env)
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["next"] == "agentself init"
    isolated = tmp_path / "isolated"
    started = run_cli(["--identity-dir", str(isolated), "init"], env)
    assert started.returncode == 0, started.stderr
    assert json.loads(started.stdout)["ok"] is True
    assert (isolated / "config.json").is_file()
    assert not (home / ".agentself" / "config.json").exists()
    again = run_cli(["show"], env)
    assert again.returncode == 2


def test_two_identities_sign_with_the_selected_address(tmp_path: Path) -> None:
    first = tmp_path / "agent-a"
    second = tmp_path / "agent-b"
    env = _isolated_env()
    assert run_cli(["--identity-dir", str(first), "init"], env).returncode == 0
    assert run_cli(["--identity-dir", str(second), "init"], env).returncode == 0
    statement = tmp_path / "statement.txt"
    statement.write_bytes(b"prove custody\n")
    first_auth = json.loads(
        run_cli(
            [
                "--identity-dir",
                str(first),
                "wallet",
                "authorize",
                "--file",
                str(statement),
            ],
            env,
        ).stdout
    )
    second_auth = json.loads(
        run_cli(
            [
                "wallet",
                "authorize",
                "--file",
                str(statement),
                "--identity-dir",
                str(second),
            ],
            env,
        ).stdout
    )
    first_addr = json.loads(
        run_cli(["--identity-dir", str(first), "wallet", "address"], env).stdout
    )["address"]
    second_addr = json.loads(
        run_cli(["wallet", "address", "--identity-dir", str(second)], env).stdout
    )["address"]
    assert first_addr != second_addr
    assert first_auth["address"] == first_addr
    assert second_auth["address"] == second_addr
    assert first_auth["message_sha256"] == sha256_text("prove custody\n")
    assert first_auth["message_sha256"] == second_auth["message_sha256"]


def test_invalid_init_id_names_the_character_rule(tmp_path: Path) -> None:
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["init", "--id", "bad id!"], env)
    assert proc.returncode == 2
    data = json.loads(proc.stdout)
    assert data["ok"] is False
    assert data["error"] == "refused"
    assert data["reason"] == (
        "invalid identity id; use letters, digits, dot, underscore, or hyphen"
    )
    assert data["next"] == "agentself init --help"


def test_authorize_out_writes_private_bytes_without_authorization(
    tmp_path: Path,
) -> None:
    env = _init(tmp_path / "vault")
    statement = tmp_path / "statement.txt"
    statement.write_bytes(b"hello\n")
    dest = tmp_path / "auth.txt"
    proc = run_cli(
        ["wallet", "authorize", "--file", str(statement), "--out", str(dest)],
        env,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert "authorization" not in data
    assert data["authorization_file"] == str(dest)
    assert data["message_sha256"] == sha256_text("hello\n")
    token = dest.read_bytes()
    assert len(token) == data["authorization_bytes"]
    assert token.startswith(b"0x")
    if os.name != "nt":
        assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    legacy = json.loads(
        run_cli(["wallet", "authorize", "--file", str(statement)], env).stdout
    )
    assert legacy["authorization"] == token.decode("utf-8")
    checked = run_cli(
        [
            "wallet",
            "verify",
            "--file",
            str(statement),
            "--authorization-file",
            str(dest),
        ],
        env,
    )
    assert checked.returncode == 0, checked.stderr
    verified = json.loads(checked.stdout)
    assert verified["valid"] is True
    assert verified["address"] == data["address"]
    blob = proc.stdout + checked.stdout + checked.stderr
    assert legacy["authorization"] not in blob
    assert "0x" + "00" not in json.dumps(verified)


def test_authorize_out_conflicts_and_legacy_positional(tmp_path: Path) -> None:
    env = _init(tmp_path / "vault")
    dest = tmp_path / "auth.txt"
    dash = run_cli(["wallet", "authorize", "hello", "--out", "-"], env)
    assert dash.returncode == 2
    assert json.loads(dash.stdout)["reason"] == "--out cannot be -; use --raw"
    raw = run_cli(["wallet", "authorize", "hello", "--out", str(dest), "--raw"], env)
    assert raw.returncode == 2
    assert json.loads(raw.stdout)["reason"] == "--raw cannot be used with --out"
    assert not dest.exists()
    legacy = run_cli(["wallet", "authorize", "hello"], env)
    assert legacy.returncode == 0
    token = json.loads(legacy.stdout)["authorization"]
    assert token.startswith("0x")
    positional = run_cli(["wallet", "verify", "hello", token], env)
    assert positional.returncode == 0
    assert json.loads(positional.stdout)["valid"] is True


def test_authorization_file_and_positional_conflict(tmp_path: Path) -> None:
    env = _init(tmp_path / "vault")
    statement = tmp_path / "statement.txt"
    statement.write_text("hello", encoding="utf-8")
    dest = tmp_path / "auth.txt"
    assert (
        run_cli(
            ["wallet", "authorize", "--file", str(statement), "--out", str(dest)],
            env,
        ).returncode
        == 0
    )
    clash = run_cli(
        [
            "wallet",
            "verify",
            "--file",
            str(statement),
            dest.read_text(encoding="utf-8"),
            "--authorization-file",
            str(dest),
        ],
        env,
    )
    assert clash.returncode == 2
    data = json.loads(clash.stdout)
    assert data["reason"] == "authorization and --authorization-file"
    assert dest.read_text(encoding="utf-8") not in clash.stdout

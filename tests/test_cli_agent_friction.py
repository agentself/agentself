"""Regression: black-box agent tasks against the installed CLI.

Fresh agents using only --help hit these: doctor missed a broken vault,
unknown binds omitted the bad value, store errors were a bare 'error',
unbound wallet address had no next step, and email connect without
--domain looked like success.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.support import apply_cli_env, cli_env, run_cli


def _corrupt_wallet_key(vault: Path) -> None:
    path = next(Path(vault).rglob("wallet.key.sops"))
    path.write_text(
        '{\n\t"data": "ENC[AES256_GCM,data:CORRUPTED"\n',
        encoding="utf-8",
    )


def _write_wallet_binding(vault: Path, name: str) -> None:
    path = Path(vault) / "config.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["wallet_backend"] = name
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def test_unknown_bind_typo_names_value_and_suggestion(tmp_path):
    env = cli_env(tmp_path / "vault")
    env["AGENTSELF_WALLET_BACKEND"] = "basee"
    proc = run_cli(["show"], env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stderr == ""
    data = json.loads(proc.stdout)
    assert data["ok"] is False
    assert "unknown wallet backend: basee" in data["reason"]
    assert "did you mean base?" in data["reason"]
    assert data["next"] == "agentself backends wallet"
    js = run_cli(["--json", "show"], env)
    assert js.stdout == proc.stdout


def test_doctor_and_wallet_surface_corrupt_wallet_key(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr
    _corrupt_wallet_key(vault)
    doctor = run_cli(["diagnose"], env)
    assert doctor.returncode == 1, doctor.stdout + doctor.stderr
    assert doctor.stderr == ""
    data = json.loads(doctor.stdout)
    assert "cannot read wallet.key" in data["reason"]
    assert "AGE-SECRET-KEY" not in doctor.stdout + doctor.stderr
    addr = run_cli(["wallet", "address"], env)
    assert addr.returncode == 1, addr.stdout + addr.stderr
    assert addr.stderr == ""
    assert json.loads(addr.stdout) == {
        "ok": False,
        "error": "error",
        "reason": "cannot read wallet.key",
        "next": "agentself diagnose",
    }
    js = run_cli(["--json", "wallet", "address"], env)
    assert js.returncode == 1, js.stdout + js.stderr
    assert json.loads(js.stdout) == json.loads(addr.stdout)
    assert "AGE-SECRET-KEY" not in js.stdout + js.stderr


def test_failed_init_does_not_persist_bind_change(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr
    _write_wallet_binding(vault, "basee")
    _corrupt_wallet_key(vault)
    proc = run_cli(["init", "--wallet", "base"], env)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    cfg = json.loads((vault / "config.json").read_text(encoding="utf-8"))
    assert cfg["wallet_backend"] == "basee"


def test_secret_create_tty_without_value_is_missing(tmp_path, monkeypatch, capsys):
    from agentself.cli.app import main

    vault = tmp_path / "vault"
    env = cli_env(vault)
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr

    class Tty:
        def isatty(self) -> bool:
            return True

        def read(self) -> str:
            raise AssertionError("must not read")

    apply_cli_env(monkeypatch, env)
    monkeypatch.setattr("agentself.cli.app.sys.stdin", Tty())
    monkeypatch.setattr("agentself.cli.io.sys.stdin", Tty())
    code = main(["secret", "create", "notes"])
    captured = capsys.readouterr()
    assert code == 3, captured.out + captured.err
    assert captured.err == ""
    data = json.loads(captured.out)
    assert data["ok"] is False
    assert data["error"] == "missing"
    assert data["reason"] == "need a value"
    assert data["next"] == "agentself secret create --help"

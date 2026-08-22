"""Agent discovery: unknown commands, optional skill, bind errors."""

from __future__ import annotations

import json

from tests.support import PROJECT_ROOT, cli_env, run_cli

SKILL = PROJECT_ROOT / "agentself" / "skills" / "agentself" / "SKILL.md"


def test_missing_subcommand_points_at_help(tmp_path):
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["secret"], env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    text = proc.stderr
    assert "next:" in text
    assert "agentself secret --help" in text


def test_unknown_command_does_not_list_aliases(tmp_path):
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["ninit"], env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    text = proc.stderr
    assert "invalid choice" in text
    assert "did you mean 'init'" in text
    for alias in ("start", "set", "get", "change", "list", "key", "identity"):
        assert f"'{alias}'" not in text, text
    for verb in (
        "init",
        "show",
        "backends",
        "diagnose",
        "secret",
        "note",
        "email",
        "wallet",
        "backup",
        "restore",
        "install",
    ):
        assert f"'{verb}'" in text, text

    nested = run_cli(["secret", "ncreate"], env)
    assert nested.returncode == 2, nested.stdout + nested.stderr
    nested_text = nested.stderr
    assert "'set'" not in nested_text
    assert "'change'" not in nested_text
    assert "'create'" in nested_text
    assert "'get'" in nested_text
    assert "'update'" in nested_text
    assert "'list'" in nested_text
    assert "'delete'" in nested_text
    assert "'exists'" in nested_text


def test_install_copies_bundled_skill(tmp_path):
    env = cli_env(tmp_path / "vault")
    bundled = SKILL.read_text(encoding="utf-8")
    bare = run_cli(["install"], env, cwd=tmp_path)
    assert bare.returncode == 2, bare.stdout + bare.stderr
    assert "next: agentself install --skills" in bare.stderr
    proc = run_cli(["install", "--skills", "--local"], env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    dest = tmp_path / ".claude" / "skills" / "agentself" / "SKILL.md"
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8") == bundled
    assert "installed" in proc.stdout
    assert not (tmp_path / ".grok" / "skills" / "agentself" / "SKILL.md").exists()

    one = run_cli(
        ["--json", "install", "--skills=agents", "--local"], env, cwd=tmp_path
    )
    assert one.returncode == 0, one.stderr
    data = json.loads(one.stdout)
    assert data["ok"] is True
    assert len(data["paths"]) == 1
    assert data["paths"][0].endswith("SKILL.md")


def test_install_unknown_target_is_actionable(tmp_path):
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["install", "--skills=nope"], env, cwd=tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "next: agentself install --help" in proc.stderr


def test_install_io_error_is_one_line(tmp_path):
    env = cli_env(tmp_path / "vault")
    (tmp_path / ".agents").write_text("blocked", encoding="utf-8")
    proc = run_cli(["install", "--skills=agents", "--local"], env, cwd=tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr
    assert "Traceback" not in proc.stdout
    lines = [line for line in proc.stderr.splitlines() if line.strip()]
    assert len(lines) == 1, proc.stderr
    assert "error" in proc.stderr.lower()

    js = run_cli(["--json", "install", "--skills=agents", "--local"], env, cwd=tmp_path)
    assert js.returncode == 1, js.stdout + js.stderr
    data = json.loads(js.stdout)
    assert data["ok"] is False
    assert data["error"] == "error"
    assert data.get("reason")


def test_backends_lists_shipped_binds_without_init(tmp_path):
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["backends"], env)
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout
    assert "AGENTSELF_WALLET_BACKEND" in text
    assert "base" in text
    assert "agentmail" in text
    assert "sops" in text
    assert "No failover" in text
    wallet = run_cli(["backends", "wallet"], env)
    assert wallet.returncode == 0, wallet.stderr
    assert "ethereum" in wallet.stdout
    assert "email" not in wallet.stdout.splitlines()[0]
    data = json.loads(run_cli(["--json", "backends"], env).stdout)
    assert data["ok"] is True
    assert data["failover"] is False
    names = [item["name"] for item in data["channels"]]
    assert names == ["wallet", "email", "store"]


def test_unknown_init_bind_points_at_backends(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    proc = run_cli(["init", "--wallet", "nope"], env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "unknown wallet backend: nope" in proc.stderr
    assert "next: agentself backends wallet" in proc.stderr
    assert list(vault.rglob("agent.agekey")) == []


def test_unknown_env_bind_on_show_points_at_backends(tmp_path):
    env = cli_env(tmp_path / "vault")
    env["AGENTSELF_WALLET_BACKEND"] = "nope"
    proc = run_cli(["show"], env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "unknown wallet backend" in proc.stderr
    assert "next: agentself backends wallet" in proc.stderr

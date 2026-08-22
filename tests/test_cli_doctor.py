"""Host catalog: identity-directory path, per-backend knobs, --version, and diagnose."""

from __future__ import annotations

import json

from agentself import __version__

from tests.support import cli_env, run_cli


def test_backends_mailbox_lists_send_holds(tmp_path):
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["backends", "email"], env)
    assert proc.returncode == 0, proc.stderr
    assert "email.send.token" in proc.stdout
    assert "email.address" in proc.stdout
    assert "imap" in proc.stdout
    assert "AGENTSELF_IMAP_HOST" in proc.stdout
    assert "AGENTSELF_SMTP_HOST" in proc.stdout
    assert "AGENTSELF_MAIL_HOST" in proc.stdout
    mailbox = run_cli(["backends", "mailbox"], env)
    assert mailbox.returncode == 2, mailbox.stdout + mailbox.stderr
    assert "did you mean 'email'" in mailbox.stderr


def test_backends_wallet_lists_ethereum_rpc(tmp_path):
    env = cli_env(tmp_path / "vault")
    proc = run_cli(["backends", "wallet"], env)
    assert proc.returncode == 0, proc.stderr
    assert "AGENTSELF_ETH_RPC_URL" in proc.stdout
    assert "ethereum" in proc.stdout


def test_doctor_json_fresh_vault(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    proc = run_cli(["--json", "diagnose"], env)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["ok"] is True
    assert data["version"] == __version__
    assert data["vault"] == str(vault)
    assert data["initialized"] is False
    assert data["tools"]["age-keygen"] is True
    assert data["tools"]["sops"] is True
    assert data["wallet_backend"] is None
    assert data["ready"]["email"] is False
    assert "AGE-SECRET-KEY" not in proc.stdout + proc.stderr
    dumped = json.dumps(data)
    assert "AGE-SECRET-KEY" not in dumped


def test_doctor_after_init_includes_binds(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr
    proc = run_cli(["--json", "diagnose"], env)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["ok"] is True
    assert data["initialized"] is True
    assert data["vault"] == str(vault)
    assert data["wallet_backend"] == "base"
    assert data["email_backend"] == "agentmail"
    assert data["store_backend"] == "sops"
    assert data["ready"]["wallet"] is True
    assert data["ready"]["email"] is False
    assert data["ready"]["store"] is True
    blob = proc.stdout + proc.stderr
    assert "AGE-SECRET-KEY" not in blob


def test_doctor_reads_binds_from_host_files(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "config.json").write_text(
        json.dumps(
            {
                "format_version": 2,
                "identity_id": "agent",
                "age_key_file": "identities/agent/agent.agekey",
                "wallet_backend": "ethereum",
                "email_backend": "agentmail",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (vault / "registry.json").write_text(
        json.dumps(
            {
                "format_version": 2,
                "identities": {
                    "agent": {
                        "id": "agent",
                        "recipient": "age1example",
                        "store_binding": "sops",
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    key = vault / "identities" / "agent" / "agent.agekey"
    key.parent.mkdir(parents=True)
    key.write_text("AGE-SECRET-KEY-SHOULD-NOT-LEAK\n", encoding="utf-8")
    env = cli_env(vault)
    proc = run_cli(["--json", "diagnose"], env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    data = json.loads(proc.stderr)
    assert data["ok"] is False
    assert data["initialized"] is True
    assert data["wallet_backend"] == "ethereum"
    assert data["email_backend"] == "agentmail"
    assert data["store_backend"] == "sops"
    assert data["reason"] == "age key file is not usable"
    blob = proc.stdout + proc.stderr
    assert "AGE-SECRET-KEY" not in blob
    human = run_cli(["diagnose"], env)
    assert human.returncode == 1, human.stdout + human.stderr
    assert "initialized: yes" in human.stdout
    assert "wallet_backend: ethereum" in human.stdout
    assert "age key file is not usable" in human.stderr
    assert "AGE-SECRET-KEY" not in human.stdout + human.stderr


def test_doctor_missing_age_keygen(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    env["PATH"] = str(empty)
    proc = run_cli(["diagnose"], env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr
    assert "Traceback" not in proc.stdout
    assert "age not on PATH" in proc.stderr
    assert "next:" in proc.stderr
    assert proc.stderr == "error: age not on PATH\nnext: agentself install --tools\n"

    js = run_cli(["--json", "diagnose"], env)
    assert js.returncode == 1, js.stdout + js.stderr
    assert js.stdout == ""
    data = json.loads(js.stderr)
    assert data["ok"] is False
    assert data["error"] == "error"
    assert data["reason"] == "age not on PATH"
    assert data["next"] == "agentself install --tools"
    assert "AGE-SECRET-KEY" not in js.stdout + js.stderr

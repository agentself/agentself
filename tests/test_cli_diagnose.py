"""Diagnose reads recorded backends from identity files and does not leak the age key."""

from __future__ import annotations

import json

from tests.support import cli_env, run_cli


def test_doctor_reads_binds_from_host_files(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "config.json").write_text(
        json.dumps(
            {
                "format_version": 1,
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
                "format_version": 1,
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
    data = json.loads(proc.stdout or proc.stderr)
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

"""Help, version, backends, secrets, and doctor must not import wallet SDKs."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from tests.support import PROJECT_ROOT, cli_env, run_cli


def _probe(script: str, env: dict[str, str] | None = None) -> list[str]:
    code = (
        script.rstrip()
        + "\n"
        + "import json, sys\n"
        + "heavy = [\n"
        + "    name for name in sys.modules\n"
        + "    if name == 'web3' or name.startswith('web3.')\n"
        + "    or name == 'eth_account' or name.startswith('eth_account.')\n"
        + "]\n"
        + "print(json.dumps(sorted(set(heavy))))\n"
    )
    merged = os.environ.copy()
    if env:
        merged.update(env)
    src = str(PROJECT_ROOT)
    pythonpath = merged.get("PYTHONPATH", "")
    merged["PYTHONPATH"] = src + os.pathsep + pythonpath if pythonpath else src
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(PROJECT_ROOT),
        env=merged,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout.splitlines()[-1])


def _run_main(argv: str) -> str:
    return (
        "from agentself.cli.app import main\n"
        "try:\n"
        f"    code = main({argv})\n"
        "except SystemExit as exc:\n"
        "    code = exc.code\n"
        "assert code in (0, None), code\n"
    )


def test_package_version_does_not_load_wallet_sdks():
    assert _probe("from agentself import __version__\nassert __version__\n") == []


def test_client_is_not_a_top_level_public_export():
    assert (
        _probe(
            "try:\n"
            "    from agentself import Client\n"
            "except ImportError:\n"
            "    pass\n"
            "else:\n"
            "    raise AssertionError('Client must remain internal')\n"
        )
        == []
    )


def test_help_does_not_load_wallet_sdks():
    assert _probe(_run_main("['--help']")) == []


def test_version_does_not_load_wallet_sdks():
    assert _probe(_run_main("['--version']")) == []


def test_backends_does_not_load_wallet_sdks():
    assert _probe(_run_main("['backends']")) == []


def test_compose_import_does_not_load_wallet_sdks():
    assert _probe("from agentself.compose import compose\n") == []


def test_wallet_factory_import_does_not_load_wallet_sdks():
    assert (
        _probe("from agentself.backends.wallet.factory import WalletAccessFactory\n")
        == []
    )


def test_secret_list_does_not_load_wallet_sdks(tmp_path):
    env = cli_env(tmp_path / "vault")
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr
    assert _probe(_run_main("['secret', 'list']"), env=env) == []


def test_doctor_does_not_load_wallet_sdks(tmp_path):
    env = cli_env(tmp_path / "vault")
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr
    assert _probe(_run_main("['--json', 'diagnose']"), env=env) == []


def test_wallet_address_loads_eth_account_not_web3(tmp_path):
    env = cli_env(tmp_path / "vault")
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr
    heavy = _probe(_run_main("['wallet', 'address']"), env=env)
    assert any(
        name == "eth_account" or name.startswith("eth_account.") for name in heavy
    )
    assert not any(name == "web3" or name.startswith("web3.") for name in heavy)

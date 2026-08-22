"""Live AgentMail is a catalog default. Pytest must never dial api.agentmail.to."""

from __future__ import annotations

import urllib.request

import pytest

from agentself.backends.email.agentmail import AgentMailMailboxAccess
from agentself.backends.email.http import refuse_live_agentmail, request
from agentself.internal.log import MemoryLog

from tests.support import cli_env, run_cli


def test_urlopen_to_agentmail_is_forbidden():
    with pytest.raises(
        AssertionError, match="live AgentMail HTTP is forbidden in tests"
    ):
        urllib.request.urlopen("https://api.agentmail.to/v0/inboxes")


def test_http_request_to_agentmail_is_forbidden():
    with pytest.raises(
        AssertionError, match="live AgentMail HTTP is forbidden in tests"
    ):
        request("https://api.agentmail.to/v0/inboxes", {})


def test_uninjected_agentmail_connect_does_not_dial_live(vault):
    mb = AgentMailMailboxAccess(vault, MemoryLog())
    with pytest.raises(
        AssertionError, match="live AgentMail HTTP is forbidden in tests"
    ):
        mb.connect("P", send_token="am_test_token_do_not_leak")


def test_cli_connect_with_token_does_not_dial_live(tmp_path):
    env = cli_env(tmp_path / "vault")
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr
    sealed = run_cli(["secret", "create", "email.send.token", "am_test_token"], env)
    assert sealed.returncode == 0, sealed.stderr
    proc = run_cli(["email", "connect"], env)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    blob = proc.stdout + proc.stderr
    assert "am_test_token" not in blob


def test_refuse_is_noop_when_env_off(monkeypatch):
    monkeypatch.delenv("AGENTSELF_FORBID_LIVE_AGENTMAIL", raising=False)
    refuse_live_agentmail("https://api.agentmail.to/v0/inboxes")

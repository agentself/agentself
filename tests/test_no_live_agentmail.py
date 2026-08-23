"""Live AgentMail is a catalog default. Pytest must never dial api.agentmail.to."""

from __future__ import annotations

import pytest

from agentself.backends.email.agentmail import AgentMailMailboxAccess
from agentself.backends.email.http import _MAX_RESPONSE_BYTES, request
from agentself.internal.log import MemoryLog

from tests.support import cli_env, run_cli, value_file


def test_uninjected_agentmail_connect_does_not_dial_live(vault):
    mb = AgentMailMailboxAccess(vault, MemoryLog())
    with pytest.raises(AssertionError, match="live HTTP is forbidden in tests"):
        mb.connect("P", credential="am_test_token_do_not_leak")


def test_cli_connect_with_token_does_not_dial_live(tmp_path):
    env = cli_env(tmp_path / "vault")
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr
    sealed = run_cli(
        [
            "secret",
            "create",
            "email.credential",
            "--file",
            value_file(tmp_path, "am_test_token"),
        ],
        env,
    )
    assert sealed.returncode == 0, sealed.stderr
    proc = run_cli(["email", "connect"], env)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    blob = proc.stdout + proc.stderr
    assert "am_test_token" not in blob


def test_request_forbids_injected_hosts(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("must not http")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(AssertionError, match="live HTTP is forbidden in tests"):
        request(
            "https://api.agentmail.to/v0/inboxes",
            {},
            forbid_hosts=("api.agentmail.to",),
        )


def test_request_rejects_oversized_response_before_parsing(monkeypatch):
    class Response:
        status = 200
        requested = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, size: int) -> bytes:
            self.requested = size
            return b"x" * size

    response = Response()
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: response)
    with pytest.raises(OSError, match="response too large"):
        request("https://example.invalid/mail", {})
    assert response.requested == _MAX_RESPONSE_BYTES + 1

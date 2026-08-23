from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests.support import App, build_app

_LIVE_HOST = "api.agentmail.to"


def _request_url(req) -> str:
    if isinstance(req, str):
        return req
    return req.full_url


@pytest.fixture(autouse=True)
def _forbid_live_agentmail(monkeypatch):
    """Never dial the live vendor.

    In-process tests inject poster/getter. This is the tripwire if they forget.
    Subprocess CLI tests get the same rule via AGENTSELF_FORBID_LIVE_AGENTMAIL.
    """

    import urllib.request

    monkeypatch.setenv("AGENTSELF_FORBID_LIVE_AGENTMAIL", "1")
    real = urllib.request.urlopen

    def guarded(req, *args, **kwargs):
        host = (urlparse(_request_url(req)).hostname or "").lower()
        if host == _LIVE_HOST:
            raise AssertionError("live AgentMail HTTP is forbidden in tests")
        return real(req, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", guarded)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir(mode=0o700)
    return root


@pytest.fixture
def app(vault: Path) -> App:
    return build_app(vault)

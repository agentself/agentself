from __future__ import annotations

import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

# Test/CI tripwire. Unset in production. Not a user knob; not listed on backends.
_FORBID_LIVE = "AGENTSELF_FORBID_LIVE_AGENTMAIL"
_LIVE_HOST = "api.agentmail.to"


def request(
    url: str,
    headers: dict[str, str],
    payload: bytes | None = None,
    *,
    method: str | None = None,
) -> tuple[int, bytes]:
    refuse_live_agentmail(url)
    req = urllib.request.Request(
        url,
        data=payload,
        headers=headers,
        method=method or ("POST" if payload is not None else "GET"),
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read() if exc.fp else b""


def refuse_live_agentmail(url: str) -> None:
    """Fail closed in the test suite. Production leaves the env unset."""

    raw = os.environ.get(_FORBID_LIVE, "").strip().lower()
    if raw in ("", "0", "false", "no", "off"):
        return
    host = (urlparse(url).hostname or "").lower()
    if host == _LIVE_HOST:
        raise AssertionError("live AgentMail HTTP is forbidden in tests")

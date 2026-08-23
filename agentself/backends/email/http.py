from __future__ import annotations

import urllib.error
import urllib.request
from urllib.parse import urlparse


def request(
    url: str,
    headers: dict[str, str],
    payload: bytes | None = None,
    *,
    method: str | None = None,
    forbid_hosts: tuple[str, ...] = (),
) -> tuple[int, bytes]:
    refuse_live_hosts(url, forbid_hosts)
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


def refuse_live_hosts(url: str, hosts: tuple[str, ...] = ()) -> None:
    """Fail closed when a hostname is listed. Production passes an empty tuple."""

    host = urlparse(url).hostname
    if host and host.lower() in {item.lower() for item in hosts}:
        raise AssertionError("live HTTP is forbidden in tests")

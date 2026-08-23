from __future__ import annotations

import urllib.error
import urllib.request
from urllib.parse import urlparse

_MAX_RESPONSE_BYTES = 1_048_576


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
            return int(resp.status), _read_limited(resp)
    except urllib.error.HTTPError as exc:
        return int(exc.code), _read_limited(exc) if exc.fp else b""


def _read_limited(stream: object) -> bytes:
    read = getattr(stream, "read", None)
    if read is None:
        raise OSError("response too large")
    try:
        body = read(_MAX_RESPONSE_BYTES + 1)
    except TypeError:
        body = read()
    if not isinstance(body, bytes) or len(body) > _MAX_RESPONSE_BYTES:
        raise OSError("response too large")
    return body


def refuse_live_hosts(url: str, hosts: tuple[str, ...] = ()) -> None:
    """Fail closed when a hostname is listed. Production passes an empty tuple."""

    host = urlparse(url).hostname
    if host and host.lower() in {item.lower() for item in hosts}:
        raise AssertionError("live HTTP is forbidden in tests")

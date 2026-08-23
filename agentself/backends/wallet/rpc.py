from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterable
from typing import Protocol

from agentself.backends.wallet.contract import WalletError

USER_AGENT = "Mozilla/5.0 (compatible; agentself/1)"
_MAX_BODY = 1_048_576


class RpcClient(Protocol):
    def request(self, method: str, params: list[object]) -> object: ...


class _TryNext(Exception):
    pass


class HttpJsonRpc:
    def __init__(
        self,
        url: str = "",
        *,
        fallbacks: list[str] | None = None,
        opener=None,
    ) -> None:
        self.url = url
        self.fallbacks = list(fallbacks or [])
        self._opener = opener

    def request(self, method: str, params: list[object]) -> object:
        urls = _dedup_urls(self.url, self.fallbacks)
        if not urls:
            raise WalletError("no RPC configured")
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        ).encode("utf-8")
        last: BaseException | None = None
        for url in urls:
            try:
                return self._post(url, payload)
            except _TryNext as exc:
                last = exc
        raise WalletError("rpc failed") from last

    def _post(self, url: str, payload: bytes) -> object:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        opener = self._opener or urllib.request.urlopen
        try:
            opened = opener(req, timeout=15)
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise _TryNext() from exc
        try:
            raw = _body_from_opened(opened)
        except _TryNext:
            raise
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise _TryNext() from exc
        try:
            if isinstance(raw, bytes):
                text = raw.decode("utf-8")
            else:
                text = str(raw)
            data = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _TryNext() from exc
        if not isinstance(data, dict) or data.get("error"):
            raise _TryNext()
        return data.get("result")


def _body_from_opened(opened: object) -> object:
    if isinstance(opened, tuple):
        status, body = opened[0], opened[1]
        _reject_http_status(status)
        return _limit_body(body)
    enter = getattr(opened, "__enter__", None)
    if enter is not None:
        with opened as resp:  # type: ignore[attr-defined]
            return _read_resp(resp)
    closer = getattr(opened, "close", None)
    try:
        return _read_resp(opened)
    finally:
        if closer is not None:
            closer()


def _read_resp(resp: object) -> object:
    status = getattr(resp, "status", None)
    if status is None:
        status = getattr(resp, "code", None)
    _reject_http_status(status)
    read = getattr(resp, "read", None)
    if read is None:
        raise _TryNext()
    try:
        blob = read(_MAX_BODY + 1)
    except TypeError:
        blob = read()
    return _limit_body(blob)


def _limit_body(body: object) -> object:
    if isinstance(body, (bytes, bytearray, str)) and len(body) > _MAX_BODY:
        raise _TryNext()
    return body


def _reject_http_status(status: object) -> None:
    if status is None:
        return
    try:
        code = int(status)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return
    if code >= 400:
        raise _TryNext()


def _dedup_urls(primary: str, fallbacks: Iterable[str] = ()) -> list[str]:
    seen: list[str] = []
    for raw in [primary, *fallbacks]:
        url = str(raw or "").strip()
        if not url or url in seen:
            continue
        seen.append(url)
    return seen

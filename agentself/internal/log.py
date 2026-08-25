from __future__ import annotations

import json
import sys
from typing import Protocol, TextIO


def _safe_label(value: str, *, fallback: str) -> str:
    """Keep internal diagnostic labels bounded and free of arbitrary text."""

    label = value.strip()
    if not label:
        return fallback
    safe = "".join(
        char
        if (
            "a" <= char <= "z"
            or "A" <= char <= "Z"
            or "0" <= char <= "9"
            or char in "._:/-"
        )
        else "_"
        for char in label
    )
    return safe[:80] or fallback


def _payload(
    operation: str,
    identity_id: str | None,
    name: str | None,
    result: str,
) -> dict[str, str | None]:
    return {
        "operation": operation,
        "identity_id": identity_id,
        "name": name,
        "result": result,
    }


class Log(Protocol):
    def record(
        self,
        operation: str,
        identity_id: str | None,
        name: str | None,
        result: str,
    ) -> None: ...


def record_diagnostic(log: Log, operation: str, exception: BaseException) -> None:
    """Record only safe context for an unexpected failure.

    Exception messages and traceback state are deliberately never inspected.
    Diagnostics use the established four-field log payload so every existing
    sink and renderer remains compatible. A broken diagnostic sink must not
    replace the original failure.
    """

    safe_operation = _safe_label(operation, fallback="unknown")
    exception_type = _safe_label(type(exception).__name__, fallback="Exception")
    try:
        log.record(safe_operation, None, None, f"unexpected:{exception_type}")
    except Exception:
        pass


class MemoryLog:
    """Never the value."""

    def __init__(self) -> None:
        self.records: list[dict[str, str | None]] = []

    def record(
        self,
        operation: str,
        identity_id: str | None,
        name: str | None,
        result: str,
    ) -> None:
        self.records.append(_payload(operation, identity_id, name, result))

    def rendered(self) -> str:
        return json.dumps(self.records, separators=(",", ":"))


class NullLog:
    """Logs are an escape hatch (AGENTSELF_LOG), never a gate."""

    def record(
        self,
        operation: str,
        identity_id: str | None,
        name: str | None,
        result: str,
    ) -> None:
        pass


class StreamLog:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stderr

    def record(
        self,
        operation: str,
        identity_id: str | None,
        name: str | None,
        result: str,
    ) -> None:
        line = json.dumps(
            _payload(operation, identity_id, name, result),
            separators=(",", ":"),
        )
        self._stream.write(line + "\n")
        self._stream.flush()

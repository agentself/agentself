from __future__ import annotations

import json
import sys
from typing import Protocol, TextIO


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

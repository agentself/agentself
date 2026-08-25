from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CliSuccess:
    payload: dict[str, object]
    redact: bool = True


@dataclass(frozen=True)
class CliRaw:
    data: str


@dataclass(frozen=True)
class CliFailure:
    exit_code: int
    error: str
    reason: str
    next: str
    extra: dict[str, object] | None = None


CliOutcome = CliSuccess | CliRaw | CliFailure

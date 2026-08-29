from __future__ import annotations


def next_object(
    command: str,
    *,
    until: str | None = None,
    interval: float | int | None = None,
) -> dict[str, object] | None:
    """Structured next step when ``command`` is an executable agentself invocation."""

    text = (command or "").strip()
    if not text.startswith("agentself "):
        return None
    payload: dict[str, object] = {"command": text}
    condition = (until or "").strip()
    if condition:
        payload["until"] = condition
    if interval is not None:
        seconds = float(interval)
        if seconds > 0:
            payload["poll_interval_seconds"] = seconds
    return payload


def attach_next(
    payload: dict[str, object],
    command: str,
    *,
    existing: object | None = None,
    until: str | None = None,
    interval: float | int | None = None,
) -> None:
    """Add ``_next`` when the string next is an agentself command."""

    if existing is not None:
        payload["_next"] = existing
        return
    obj = next_object(command, until=until, interval=interval)
    if obj is not None:
        payload["_next"] = obj

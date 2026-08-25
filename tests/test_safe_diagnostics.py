"""Unexpected-failure diagnostics never include exception details."""

from __future__ import annotations

import io
import json

from agentself.cli import app
from agentself.internal.log import MemoryLog, StreamLog, record_diagnostic


def test_memory_diagnostic_keeps_only_safe_operation_and_exception_type() -> None:
    secret = "credential-value-must-not-appear"
    log = MemoryLog()

    record_diagnostic(log, "email:connect", RuntimeError(secret))

    assert log.records == [
        {
            "operation": "email:connect",
            "identity_id": None,
            "name": None,
            "result": "unexpected:RuntimeError",
        }
    ]
    assert secret not in log.rendered()


def test_stream_diagnostic_preserves_four_field_json_schema() -> None:
    secret = "private-key-must-not-appear"
    stream = io.StringIO()
    log = StreamLog(stream)

    record_diagnostic(log, "wallet:authorize", ValueError(secret))

    assert json.loads(stream.getvalue()) == {
        "operation": "wallet:authorize",
        "identity_id": None,
        "name": None,
        "result": "unexpected:ValueError",
    }
    assert secret not in stream.getvalue()


def test_diagnostic_bounds_and_sanitizes_operation_context() -> None:
    class LegacyLog:
        def __init__(self) -> None:
            self.records: list[tuple[str, str | None, str | None, str]] = []

        def record(
            self,
            operation: str,
            identity_id: str | None,
            name: str | None,
            result: str,
        ) -> None:
            self.records.append((operation, identity_id, name, result))

    log = LegacyLog()
    record_diagnostic(
        log,
        "email connect with secret " + ("x" * 200),
        LookupError("do not log"),
    )

    assert len(log.records) == 1
    operation, identity_id, name, result = log.records[0]
    assert operation == "email_connect_with_secret_" + ("x" * 54)
    assert len(operation) == 80
    assert identity_id is None
    assert name is None
    assert result == "unexpected:LookupError"


def test_broken_log_sink_cannot_mask_unexpected_failure() -> None:
    class BrokenLog:
        def record(
            self,
            operation: str,
            identity_id: str | None,
            name: str | None,
            result: str,
        ) -> None:
            del operation, identity_id, name, result
            raise OSError("diagnostic sink unavailable")

    record_diagnostic(BrokenLog(), "wallet:send", RuntimeError("do not log"))


def test_cli_unexpected_failure_is_generic_and_diagnosable(
    tmp_path, monkeypatch, capsys
) -> None:
    canary = "runtime-secret-must-not-appear"
    monkeypatch.setenv("AGENTSELF_LOG", "1")
    monkeypatch.setenv("AGENTSELF_IDENTITY_DIR", str(tmp_path / "identity"))

    from agentself.internal import host_tools

    monkeypatch.setattr(host_tools, "ensure_host_tools", lambda fetch=False: None)
    monkeypatch.setattr(app, "missing_host_tool", lambda *args, **kwargs: None)

    def raising_handler(_args, _vault):
        raise RuntimeError(canary)

    monkeypatch.setattr(app, "_load_handler", lambda _spec: raising_handler)

    assert app.main(["--json", "show"]) == 1
    captured = capsys.readouterr()

    assert json.loads(captured.out) == {
        "ok": False,
        "error": "error",
        "reason": "error",
        "next": "agentself diagnose",
    }
    assert json.loads(captured.err) == {
        "operation": "show",
        "identity_id": None,
        "name": None,
        "result": "unexpected:RuntimeError",
    }
    assert canary not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err
    assert "runtime-secret" not in captured.out + captured.err

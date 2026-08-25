from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentself.cli import app
from agentself.cli.registry import CommandSpec


def test_load_handler_imports_lazy_reference_and_returns_callable(monkeypatch):
    calls: list[str] = []

    def handler(_args, _vault):
        return None

    def load(module_name: str):
        calls.append(module_name)
        return SimpleNamespace(run=handler)

    monkeypatch.setattr(app.importlib, "import_module", load)
    spec = CommandSpec(("test",), "", "example.handlers:run")

    loaded = app._load_handler(spec)

    assert loaded is handler
    assert calls == ["example.handlers"]


@pytest.mark.parametrize(
    "handler_ref",
    [None, "", "example.handlers", ":run", "example.handlers:"],
)
def test_load_handler_rejects_missing_handler_reference(handler_ref):
    spec = CommandSpec(("test",), "", handler_ref)

    with pytest.raises(TypeError):
        app._load_handler(spec)


def test_load_handler_rejects_non_callable_attribute(monkeypatch):
    monkeypatch.setattr(
        app.importlib,
        "import_module",
        lambda _module_name: SimpleNamespace(run="not a handler"),
    )
    spec = CommandSpec(("test",), "", "example.handlers:run")

    with pytest.raises(TypeError, match="not callable"):
        app._load_handler(spec)

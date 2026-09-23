"""The development lock is checked in and does not pin the published package."""

from __future__ import annotations

from tests.support import PROJECT_ROOT

WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "test.yml"


def test_project_dependency_stays_a_range_while_the_lock_pins_it() -> None:
    project = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dependencies = project.split("[dependency-groups]", 1)[0]
    assert "eth-account>=0.13.7,<0.15" in dependencies
    assert "eth-account==" not in dependencies
    lock = (PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "eth-account"' in lock
    assert 'name = "pytest"' in lock
    assert 'name = "ruff"' in lock
    assert 'name = "mypy"' in lock
    assert 'name = "twine"' in lock
    assert 'name = "setuptools"' in lock


def test_ci_checks_the_lock_and_keeps_wheel_acceptance() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "uv lock --check" in text
    assert "uv sync --locked" in text
    assert 'version: "0.11.14"' in text
    assert "python -m build --no-isolation" in text
    assert 'AGENTSELF_ACCEPTANCE_EXE="$venv/bin/agentself"' in text
    assert '"$venv/bin/python" -m pytest -q acceptance' in text
    artifact = text.split("artifact:", 1)[1]
    assert "pip install -e" not in artifact
    assert "pip install dist/*.whl pytest" in artifact

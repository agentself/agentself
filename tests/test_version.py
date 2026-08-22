"""Version strings stay in lockstep."""

from __future__ import annotations

import tomllib

from tests.support import PROJECT_ROOT


def test_version_is_single_sourced():
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    assert "version" not in project
    assert "version" in project["dynamic"]
    assert (
        data["tool"]["setuptools"]["dynamic"]["version"]["attr"]
        == "agentself.__version__"
    )

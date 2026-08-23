"""Keep release CI dependencies pinned and downloaded host tools verified."""

from __future__ import annotations

import re

from tests.support import PROJECT_ROOT

WORKFLOWS = (
    PROJECT_ROOT / ".github" / "workflows" / "test.yml",
    PROJECT_ROOT / ".github" / "workflows" / "publish.yml",
)
SHA256 = re.compile(r"[0-9a-f]{64}")


def test_workflow_actions_use_immutable_refs() -> None:
    refs: list[str] = []
    for path in WORKFLOWS:
        refs.extend(re.findall(r"uses:\s*([^\s#]+)", path.read_text(encoding="utf-8")))

    assert refs
    for ref in refs:
        if ref.startswith("./"):
            assert ref.endswith(".yml") or ref.endswith(".yaml")
            continue
        assert re.search(r"@[0-9a-f]{40}$", ref), ref


def test_publish_does_not_skip_existing_testpypi_artifacts() -> None:
    text = WORKFLOWS[1].read_text(encoding="utf-8")
    assert "skip-existing" not in text


def test_ci_host_tool_downloads_have_pinned_digests() -> None:
    linux = (PROJECT_ROOT / ".github" / "scripts" / "linux-host-tools.sh").read_text(
        encoding="utf-8"
    )
    test_workflow = WORKFLOWS[0].read_text(encoding="utf-8")

    assert "sha256sum --check --status" in linux
    assert "shasum -a 256 -c" in test_workflow
    assert "Get-FileHash -Algorithm SHA256" in test_workflow
    assert SHA256.findall(linux)
    assert SHA256.findall(test_workflow)


def test_publish_is_gated_on_tested_dist_for_that_sha() -> None:
    publish = WORKFLOWS[1].read_text(encoding="utf-8")
    test = WORKFLOWS[0].read_text(encoding="utf-8")
    assert "uses: ./.github/workflows/test.yml" in publish
    assert "python -m build" not in publish
    assert "pip install build" not in publish
    assert "id-token: write" in publish
    assert "PYPI_API_TOKEN" not in publish
    assert "pypi-token" not in publish.lower()
    assert "password:" not in publish
    assert "workflow_call:" in test
    assert "name: python-package-distributions" in test
    assert "upload-artifact@" in test
    assert "needs: [lint, test]" in test
    assert "needs: ci" in publish or "needs: [ci" in publish


def test_test_workflow_does_not_double_run_same_repo_prs() -> None:
    """push on every branch plus pull_request runs the matrix twice."""

    trigger = WORKFLOWS[0].read_text(encoding="utf-8").split("\njobs:", 1)[0]
    assert re.search(
        r"push:\s*\n(?:[ \t]+\S.*\n)*?[ \t]+branches:\s*\n[ \t]+-\s+main\b",
        trigger,
    ), trigger
    assert re.search(r"branches:\s*\n[ \t]+-\s+[\'\"]\*\*[\'\"]", trigger) is None
    assert "pull_request:" in trigger
    assert "workflow_call:" in trigger
    assert "workflow_dispatch:" in trigger
    assert "merge_group:" in trigger

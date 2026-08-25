"""Keep release CI dependencies pinned and downloaded host tools verified."""

from __future__ import annotations

import re

from tests.support import PROJECT_ROOT

WORKFLOWS = (
    PROJECT_ROOT / ".github" / "workflows" / "test.yml",
    PROJECT_ROOT / ".github" / "workflows" / "publish.yml",
)
SCRIPTS = PROJECT_ROOT / ".github" / "scripts"
SHA256 = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
OVERLONG_HEX = re.compile(r"[0-9a-f]{65,}", re.IGNORECASE)
ASSIGNED_DIGEST = re.compile(
    r"(?:age_sha256|sops_sha256|ageSha256|sopsSha256)\s*=\s*\"?([0-9a-fA-F]+)\"?"
)
JOB_ID = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")


def _jobs(text: str) -> dict[str, str]:
    _, _, rest = text.partition("\njobs:\n")
    jobs: dict[str, str] = {}
    current: str | None = None
    chunks: list[str] = []
    for line in rest.splitlines(keepends=True):
        match = JOB_ID.match(line)
        if match:
            if current is not None:
                jobs[current] = "".join(chunks)
            current = match.group(1)
            chunks = []
        else:
            chunks.append(line)
    if current is not None:
        jobs[current] = "".join(chunks)
    return jobs


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
    linux = (SCRIPTS / "linux-host-tools.sh").read_text(encoding="utf-8")
    macos = (SCRIPTS / "macos-host-tools.sh").read_text(encoding="utf-8")
    windows = (SCRIPTS / "windows-host-tools.ps1").read_text(encoding="utf-8")
    test_workflow = WORKFLOWS[0].read_text(encoding="utf-8")

    assert "sha256sum --check --status" in linux
    assert "shasum -a 256 -c" in macos
    assert "Get-FileHash -Algorithm SHA256" in windows
    assert "linux-host-tools.sh" in test_workflow
    assert "macos-host-tools.sh" in test_workflow
    assert "windows-host-tools.ps1" in test_workflow
    # macos-latest is arm64. A 65-char darwin-arm64 sops digest once broke
    # only that job; {64} still matches a prefix of an overlong hex string.
    assert "v1.3.1:arm64)" in macos
    assert "v3.13.3:arm64)" in macos
    for name, text, count in (
        ("linux-host-tools.sh", linux, 4),
        ("macos-host-tools.sh", macos, 4),
        ("windows-host-tools.ps1", windows, 2),
    ):
        overlong = OVERLONG_HEX.findall(text)
        assert overlong == [], (name, overlong)
        assigned = ASSIGNED_DIGEST.findall(text)
        assert assigned, name
        for digest in assigned:
            assert len(digest) == 64, (name, digest)
            assert SHA256.fullmatch(digest.lower()), (name, digest)
        found = SHA256.findall(text.lower())
        assert len(found) == count, (name, found)


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


def test_changelog_and_release_fences_are_balanced() -> None:
    for name in ("CHANGELOG.md", "RELEASE.md"):
        path = PROJECT_ROOT / name
        lines = path.read_text(encoding="utf-8").splitlines()
        fences = 0
        for line in lines:
            assert line != "```n", path
            assert not line.startswith("```n"), path
            if line.startswith("```"):
                fences += 1
        assert fences % 2 == 0, (name, fences)


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
    assert "concurrency:" in trigger
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in trigger


def test_pull_requests_skip_macos_and_second_windows() -> None:
    """macOS is 10x billed minutes; a second Windows cell is 2x."""

    jobs = _jobs(WORKFLOWS[0].read_text(encoding="utf-8"))
    assert "os: macos-latest" not in jobs["test"]
    assert "os: windows-latest" in jobs["test"]
    assert 'python-version: "3.11"' in jobs["test"]
    assert 'python-version: "3.12"' in jobs["test"]
    assert "os: macos-latest" in jobs["test-full"]
    assert "os: windows-latest" in jobs["test-full"]
    assert 'python-version: "3.11"' in jobs["test-full"]
    assert "if: github.event_name != 'pull_request'" in jobs["test-full"]
    assert "timeout-minutes:" in jobs["lint"]
    assert "timeout-minutes:" in jobs["test"]
    assert "cache: pip" in jobs["test"]
    assert "retention-days: 7" in jobs["artifact"]

"""Keep release CI dependencies pinned and downloaded host tools verified."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    ROOT / ".github" / "workflows" / "test.yml",
    ROOT / ".github" / "workflows" / "publish.yml",
)
SHA256 = re.compile(r"[0-9a-f]{64}")


def test_workflow_actions_use_immutable_refs() -> None:
    refs: list[str] = []
    for path in WORKFLOWS:
        refs.extend(
            re.findall(r"uses:\s*([^\s#]+)", path.read_text(encoding="utf-8"))
        )

    assert refs
    assert all(re.search(r"@[0-9a-f]{40}$", ref) for ref in refs)


def test_ci_host_tool_downloads_have_pinned_digests() -> None:
    linux = (ROOT / ".github" / "scripts" / "linux-host-tools.sh").read_text(
        encoding="utf-8"
    )
    test_workflow = WORKFLOWS[0].read_text(encoding="utf-8")

    assert "sha256sum --check --status" in linux
    assert "shasum -a 256 -c" in test_workflow
    assert "Get-FileHash -Algorithm SHA256" in test_workflow
    assert len(SHA256.findall(linux)) >= 4
    assert len(SHA256.findall(test_workflow)) >= 6

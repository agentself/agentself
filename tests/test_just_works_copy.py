"""Cold-start docs install host tools and authorize; they do not send first."""

from __future__ import annotations

from tests.support import PROJECT_ROOT

README = PROJECT_ROOT / "README.md"
SKILL_DIR = PROJECT_ROOT / "agentself" / "skills" / "agentself"
SKILL = SKILL_DIR / "SKILL.md"
EMAIL_CONNECT = SKILL_DIR / "references" / "email-connect.md"


def _readme_paste_prompt() -> str:
    _, rest = README.read_text(encoding="utf-8").split("```text\n", 1)
    return rest.split("```", 1)[0]


def _missing_cli_section() -> str:
    text = SKILL.read_text(encoding="utf-8")
    start = text.index("If `agentself` is missing")
    end = text.index("The CLI writes one JSON")
    return text[start:end]


def test_readme_paste_prompt_installs_tools_and_recovers_path() -> None:
    fence = _readme_paste_prompt()
    assert "install --tools" in fence
    assert "uv tool update-shell" in fence


def test_readme_paste_prompt_first_wallet_action_is_authorize_not_send() -> None:
    fence = _readme_paste_prompt()
    assert "wallet authorize --file" in fence
    assert "wallet verify" in fence
    assert "not `wallet send`" in fence
    # The fence names send only as a negation; authorize must come first.
    if "wallet send" in fence:
        assert fence.index("wallet authorize --file") < fence.index("wallet send")


def test_skill_missing_cli_section_installs_tools() -> None:
    section = _missing_cli_section()
    assert "install --tools" in section
    assert "uv tool update-shell" in section


def test_email_connect_documents_continue_and_happy_path() -> None:
    text = EMAIL_CONNECT.read_text(encoding="utf-8")
    assert "--continue --state" in text
    assert "--result-file" in text
    assert "## Happy path (five JSON round-trips)" in text
    _, rest = text.split("## Happy path (five JSON round-trips)", 1)
    happy = rest.split("## ", 1)[0]
    assert "email connect" in happy
    assert "--continue --state" in happy
    assert "--result-file" in happy
    assert "email show" in happy


def test_readme_llm_first_line_installs_tools_and_recovers_path() -> None:
    line1 = README.read_text(encoding="utf-8").splitlines()[0]
    assert "install --tools" in line1
    assert "uv tool update-shell" in line1


def test_skill_first_wallet_demo_is_authorize_not_send() -> None:
    text = SKILL.read_text(encoding="utf-8")
    _, rest = text.split("## Common path", 1)
    common = rest.split("## Open the relevant workflow", 1)[0]
    assert "wallet authorize --file" in common
    assert "wallet verify" in common
    assert "do not send" in common
    assert "default Base wallet is live" in common


def test_readme_sops_default_includes_windows() -> None:
    text = README.read_text(encoding="utf-8")
    start = text.index("| Capability | Default | Alternatives |")
    nearby = text[start : start + 600]
    assert "`sops`" in nearby
    assert "`pass`" in nearby
    assert "including Windows" in nearby
    assert "not the Windows default" in nearby


def test_install_skills_tree_is_four_files() -> None:
    files = sorted(
        path.relative_to(SKILL_DIR).as_posix()
        for path in SKILL_DIR.rglob("*")
        if path.is_file()
    )
    assert files == [
        "SKILL.md",
        "references/email-connect.md",
        "references/handoff.md",
        "references/mail.md",
    ]

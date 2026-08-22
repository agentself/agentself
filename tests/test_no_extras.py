"""Day-1 extras ship list is empty. Phone stays not a DID."""

from __future__ import annotations

from tests.support import PROJECT_ROOT

BANNED_STEMS = (
    "github",
    "telegram",
    "did_key",
    "didkey",
    "nostr",
    "alby",
    "bluesky",
    "passkey",
    "calendar",
)

BANNED_PACKAGES = (
    "github_access",
    "telegram_access",
    "nostr_access",
    "alby_access",
    "did_key",
    "didkey",
    "ens_access",
    "bluesky_access",
)

PROD_DIRS = (
    "agentself",
    "agentself/internal",
)

FORBIDDEN_ROOT = (
    "store_access",
    "custody_manager",
    "mailbox_access",
    "sms_access",
    "wallet_access",
    "principal_access",
    "gateway",
    "utilities",
)


def test_no_new_channel_modules_for_excluded_extras():
    top = {path.name for path in PROJECT_ROOT.iterdir() if path.is_dir()}
    for name in BANNED_PACKAGES:
        assert name not in top

    for folder in PROD_DIRS:
        root = PROJECT_ROOT / folder
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            stem = path.stem.lower().replace("-", "_")
            for banned in BANNED_STEMS:
                assert banned not in stem, f"{path} looks like extra {banned}"


def test_method_packages_are_not_at_repo_root():
    top = {path.name for path in PROJECT_ROOT.iterdir() if path.is_dir()}
    for name in FORBIDDEN_ROOT:
        assert name not in top, f"{name} still at repo root"

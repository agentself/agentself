from __future__ import annotations

import os
import subprocess

from agentself.host import ENV_AGE_KEY_FILE, ENV_IDENTITY_ID
from agentself.internal.custody.errors import UnboundCaller
from agentself.internal.files import run_resolved
from agentself.internal.types import BoundCaller


def bind_from_env() -> BoundCaller:
    """Private key is never a request body."""

    identity_id = os.environ.get(ENV_IDENTITY_ID, "").strip()
    key_file = os.environ.get(ENV_AGE_KEY_FILE, "").strip()
    if not identity_id or not key_file:
        raise UnboundCaller("not initialized")
    return BoundCaller(identity_id, public_recipient(key_file))


def public_recipient(key_file: str) -> str:
    if not os.path.isfile(key_file) or os.path.basename(key_file).startswith("-"):
        raise UnboundCaller("not initialized")
    try:
        proc = run_resolved(["age-keygen", "-y", key_file], timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UnboundCaller("not initialized") from exc
    recipient = proc.stdout.decode("utf-8").strip()
    if proc.returncode != 0 or not recipient.startswith("age1"):
        raise UnboundCaller("not initialized")
    return recipient

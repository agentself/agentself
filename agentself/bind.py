from __future__ import annotations

import os
import subprocess

from agentself.host import ENV_AGE_KEY_FILE, ENV_IDENTITY_ID
from agentself.internal.custody.errors import UnboundCaller
from agentself.internal.files import resolve_tool
from agentself.internal.types import BoundCaller


def bind_from_env() -> BoundCaller:
    """Private key is never a request body."""

    principal_id = os.environ.get(ENV_IDENTITY_ID, "").strip()
    key_file = os.environ.get(ENV_AGE_KEY_FILE, "").strip()
    if not principal_id or not key_file:
        raise UnboundCaller("unbound caller")
    return BoundCaller(principal_id, public_recipient(key_file))


def public_recipient(key_file: str) -> str:
    if not os.path.isfile(key_file):
        raise UnboundCaller("unbound caller")
    if os.path.basename(key_file).startswith("-"):
        raise UnboundCaller("unbound caller")
    try:
        proc = subprocess.run(
            [resolve_tool("age-keygen"), "-y", key_file],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UnboundCaller("unbound caller") from exc
    recipient = proc.stdout.decode("utf-8").strip()
    if proc.returncode != 0 or not recipient.startswith("age1"):
        raise UnboundCaller("unbound caller")
    return recipient

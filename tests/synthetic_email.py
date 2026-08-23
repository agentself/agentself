from __future__ import annotations

import secrets
from typing import ClassVar

from agentself.backends.email.contract import (
    MailboxAccess,
    mailbox_view,
    require_addr,
    setup_failed,
    setup_needed,
)
from agentself.internal.names import EMAIL_CREDENTIAL_NAME, require_safe_token
from agentself.internal.setup import (
    SETUP_ACTION_REQUIRED,
    SETUP_PENDING,
    credential_option,
    setup_option,
)

CONTINUATION_CANARY = "oauth-refresh-CANARY-do-not-leak"
CREDENTIAL_CANARY = "oauth-access-CANARY-do-not-leak"

OPTIONS = (
    setup_option(
        name="label",
        type="string",
        required=True,
        persist=True,
        persist_as="email.oauthish.label",
        help="Display label for this mailbox.",
        prompt="Mailbox label",
    ),
    credential_option(
        persist=True,
        persist_as=EMAIL_CREDENTIAL_NAME,
        help="Access token. Write it to --result-file and continue.",
    ),
)


class SyntheticEmailAccess(MailboxAccess):
    """OAuth-shaped setup via opaque continuation. Not a shipped bind."""

    received_states: ClassVar[list[object]] = []
    issued: ClassVar[list[object]] = []

    def send(
        self,
        identity_id: str,
        to: str,
        subject: str,
        body: str,
        credential: str | None = None,
        address: str | None = None,
    ) -> None:
        require_safe_token(identity_id, "identity id")
        require_addr(to)
        del subject, body, credential, address

    def receive(
        self,
        identity_id: str,
        *,
        credential: str | None = None,
        address: str | None = None,
        message_id: str | None = None,
    ) -> list[dict[str, str]]:
        require_safe_token(identity_id, "identity id")
        del credential, address, message_id
        return []

    def list(
        self,
        identity_id: str,
        *,
        credential: str | None = None,
        address: str | None = None,
    ) -> list[dict[str, str]]:
        require_safe_token(identity_id, "identity id")
        del credential, address
        return []

    def describe(
        self,
        identity_id: str,
        *,
        credential: str | None = None,
        address: str | None = None,
    ) -> dict[str, object]:
        require_safe_token(identity_id, "identity id")
        wanted = (address or "").strip()
        if wanted:
            return mailbox_view(wanted, owned_address=True)
        return mailbox_view()

    def setup_options(self) -> tuple[dict[str, object], ...]:
        return OPTIONS

    def connect(
        self,
        identity_id: str,
        *,
        credential: str | None = None,
        address: str | None = None,
        answers: dict[str, str] | None = None,
        state: object | None = None,
    ) -> dict[str, object]:
        require_safe_token(identity_id, "identity id")
        del address
        type(self).received_states.append(state)
        extra = dict(answers or {})
        blob = state if isinstance(state, dict) else None
        if blob is None or not blob.get("phase") or not blob.get("nonce"):
            return self._issue(
                None,
                status=SETUP_ACTION_REQUIRED,
                human_action_required=True,
                message="Authorize this identity in the provider",
                continuation=_continuation("action"),
            )
        nonce = str(blob.get("nonce") or "")
        phase = str(blob.get("phase") or "")
        canary = blob.get("refresh")
        if canary != CONTINUATION_CANARY:
            return setup_failed("unknown setup")
        if phase == "action":
            return self._issue(
                None,
                status=SETUP_PENDING,
                message="Waiting for authorization",
                continuation=_continuation("pending", nonce),
            )
        if phase == "pending":
            return self._issue(
                _named("label"),
                continuation=_continuation("label", nonce),
            )
        if phase == "label":
            label = (extra.get("label") or "").strip()
            if not label:
                return self._issue(
                    _named("label"),
                    continuation=dict(blob),
                )
            token = (credential or extra.get("credential") or "").strip()
            if not token:
                nxt = _continuation("credential", nonce)
                nxt["label"] = label
                return self._issue(_named("credential"), continuation=nxt)
            return mailbox_view(f"{label}@example.com", owned_address=True)
        if phase == "credential":
            token = (credential or extra.get("credential") or "").strip()
            if not token:
                return self._issue(_named("credential"), continuation=dict(blob))
            label = str(blob.get("label") or extra.get("label") or "agent").strip()
            return mailbox_view(f"{label}@example.com", owned_address=True)
        return setup_failed("unknown setup")

    def _issue(
        self,
        option: dict[str, object] | None,
        *,
        continuation: dict[str, object],
        status: str | None = None,
        human_action_required: bool = False,
        message: str = "",
    ) -> dict[str, object]:
        type(self).issued.append(continuation)
        if status is None:
            return setup_needed(
                option,
                human_action_required=human_action_required,
                message=message,
                continuation=continuation,
            )
        return setup_needed(
            option,
            status=status,
            human_action_required=human_action_required,
            message=message,
            continuation=continuation,
        )


def _named(name: str) -> dict[str, object]:
    for item in OPTIONS:
        if item.get("name") == name:
            return dict(item)
    raise KeyError(name)


def _continuation(phase: str, nonce: str | None = None) -> dict[str, object]:
    return {
        "phase": phase,
        "nonce": nonce or secrets.token_hex(8),
        "refresh": CONTINUATION_CANARY,
    }

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from agentself.internal.setup import SETUP_FAILED, SETUP_INPUT_REQUIRED


class MailboxError(Exception):
    """Mailbox Resource failure. Must never include a secret or credential value."""


def secret_or_env(value: str | None, env_name: str) -> str:
    token = (value or "").strip()
    return token or os.environ.get(env_name, "").strip()


def require_addr(to: str) -> None:
    if not to or "@" not in to or "\n" in to or "\r" in to:
        raise MailboxError("invalid recipient")


def require_secret(value: str | None) -> str:
    token = value or ""
    if not token:
        raise MailboxError("missing credentials")
    if any(ord(ch) < 32 for ch in token):
        raise MailboxError("invalid credentials")
    return token


def mailbox_view(
    address: str | None = None,
    *,
    owned_address: bool = False,
    needs_domain: bool = False,
    status: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "address": address,
        "owned_address": owned_address,
        "needs_domain": needs_domain,
    }
    if status:
        payload["status"] = status
    return payload


def setup_needed(
    option: dict[str, object] | None = None,
    *,
    status: str = SETUP_INPUT_REQUIRED,
    human_action_required: bool = False,
    message: str = "",
    continuation: object | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": status,
        "address": None,
        "owned_address": False,
        "needs_domain": False,
        "human_action_required": human_action_required,
    }
    if option:
        payload["option"] = dict(option)
    if message:
        payload["message"] = message
    if continuation is not None:
        payload["continuation"] = continuation
    return payload


def setup_failed(reason: str = "error") -> dict[str, object]:
    return {
        "status": SETUP_FAILED,
        "address": None,
        "owned_address": False,
        "needs_domain": False,
        "reason": reason,
    }


class MailboxAccess(ABC):
    """Caller never names the inbound/outbound Resource."""

    @abstractmethod
    def send(
        self,
        identity_id: str,
        to: str,
        subject: str,
        body: str,
        credential: str | None = None,
        address: str | None = None,
    ) -> None: ...

    @abstractmethod
    def receive(
        self,
        identity_id: str,
        *,
        credential: str | None = None,
        address: str | None = None,
        message_id: str | None = None,
    ) -> list[dict[str, str]]:
        """May consume new mail."""

    @abstractmethod
    def list(
        self,
        identity_id: str,
        *,
        credential: str | None = None,
        address: str | None = None,
    ) -> list[dict[str, str]]:
        """Metadata, not a mailbox product."""

    @abstractmethod
    def describe(
        self,
        identity_id: str,
        *,
        credential: str | None = None,
        address: str | None = None,
    ) -> dict[str, object]: ...

    def setup_options(self) -> tuple[dict[str, object], ...]:
        return ()

    def connect(
        self,
        identity_id: str,
        *,
        credential: str | None = None,
        address: str | None = None,
        answers: dict[str, str] | None = None,
        state: object | None = None,
    ) -> dict[str, object]:
        """Return a mailbox view when connected, or setup_needed otherwise.

        Incomplete setup may return opaque `continuation` for the next call.
        `state` is that continuation from a previous incomplete setup.
        Resumable failures must return setup_needed, not raise MailboxError.
        Transient rpc MailboxError is also resumable; other MailboxError is
        terminal.
        """

        del answers, state
        return self.describe(identity_id, credential=credential, address=address)

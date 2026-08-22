from __future__ import annotations

from abc import ABC, abstractmethod

from agentself.internal.setup import (
    SETUP_FAILED,
    SETUP_INPUT_REQUIRED,
    setup_status_of,
)


class MailboxError(Exception):
    """Mailbox Resource failure. Must never include a secret or send-token value."""


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
        principal_id: str,
        to: str,
        subject: str,
        body: str,
        send_token: str | None = None,
        address: str | None = None,
    ) -> None: ...

    @abstractmethod
    def recv(
        self,
        principal_id: str,
        *,
        send_token: str | None = None,
        address: str | None = None,
        message_id: str | None = None,
    ) -> list[dict[str, str]]:
        """May consume new mail."""

    @abstractmethod
    def list(
        self,
        principal_id: str,
        *,
        send_token: str | None = None,
        address: str | None = None,
    ) -> list[dict[str, str]]:
        """Metadata, not a mailbox product."""

    @abstractmethod
    def describe(
        self,
        principal_id: str,
        *,
        send_token: str | None = None,
        address: str | None = None,
    ) -> dict[str, object]: ...

    def connect(
        self,
        principal_id: str,
        *,
        send_token: str | None = None,
        address: str | None = None,
        answers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """Generic setup. Default is describe(); no create.

        Return a mailbox view when connected. Return setup_needed() when the
        backend needs input, a human action, or a later continuation. Public
        callers never see provider workflow names.
        """

        del answers
        return self.describe(principal_id, send_token=send_token, address=address)


def connect_status(payload: object) -> str:
    return setup_status_of(payload)

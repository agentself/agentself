from __future__ import annotations

from abc import ABC, abstractmethod


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
        raise MailboxError("rpc failed")
    return token


def mailbox_view(
    address: str | None = None,
    *,
    owned_address: bool = False,
    needs_domain: bool = False,
) -> dict[str, object]:
    return {
        "address": address,
        "owned_address": owned_address,
        "needs_domain": needs_domain,
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
    ) -> None:
        ...

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
    ) -> dict[str, object]:
        ...

    def connect(
        self,
        principal_id: str,
        *,
        send_token: str | None = None,
        address: str | None = None,
    ) -> dict[str, object]:
        """Default is describe(); no create."""
        return self.describe(principal_id, send_token=send_token, address=address)

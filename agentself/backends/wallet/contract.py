from __future__ import annotations

from abc import ABC, abstractmethod


class WalletError(Exception):
    """Wallet Resource failure. Must never include a private key."""


class CannotAuthorize(WalletError):
    def __init__(self, message: str = "backend cannot authorize") -> None:
        super().__init__(message)


class CannotSend(WalletError):
    def __init__(self, message: str = "backend cannot send") -> None:
        super().__init__(message)


class WalletAccess(ABC):
    """Caller never names the backend."""

    @abstractmethod
    def address(self, identity_id: str) -> str: ...

    @abstractmethod
    def authorize(self, identity_id: str, message: str) -> str: ...

    @abstractmethod
    def balance(self, identity_id: str) -> dict[str, str]:
        """Amount and asset as strings. Extra keys are allowed."""

    @abstractmethod
    def send(self, identity_id: str, to: str, amount: str, asset: str) -> None: ...

    @abstractmethod
    def describe(self, identity_id: str) -> dict[str, object]: ...

    @abstractmethod
    def verify(
        self, identity_id: str, message: str, authorization: str
    ) -> dict[str, object]:
        """Confirm an authorization against this identity. Never names a vendor."""

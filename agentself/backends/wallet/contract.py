from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class WalletError(Exception):
    """Wallet Resource failure. Must never include a private key."""


class CannotAuthorize(WalletError):
    def __init__(self, message: str = "backend cannot authorize") -> None:
        super().__init__(message)


class CannotSend(WalletError):
    def __init__(
        self, message: str = "backend cannot send", reason: str = "cannot_send"
    ) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class WalletMaterial:
    name: str


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
    def send(self, identity_id: str, to: str, amount: str, asset: str) -> str:
        """Send and return the asset actually used. Empty asset is the backend default."""

    def payment_ref(self) -> str:
        """Transaction hash from the last send, if this backend has one."""

        return ""

    @abstractmethod
    def describe(self, identity_id: str) -> dict[str, object]: ...

    @abstractmethod
    def verify(
        self, identity_id: str, message: str, authorization: str
    ) -> dict[str, object]:
        """Confirm an authorization against this identity. Never names a vendor."""

    def required_material(self) -> WalletMaterial | None:
        """None means no store material (remote / watch-only)."""

        return None

    def create_material(self) -> str:
        """Produce a new material value. Only called if required and missing."""

        raise WalletError("backend cannot create material")

    def bind_material(self, value: str) -> None:
        """Receive store material. Default no-op."""

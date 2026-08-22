from __future__ import annotations

from abc import ABC, abstractmethod


class StoreError(Exception):
    """Store Resource failure. Must never include a secret value."""


class HoldNameMissing(StoreError):
    pass


class HoldNameExists(StoreError):
    pass


class StoreResourceError(StoreError):
    pass


class StoreAccess(ABC):
    """Caller never names the Resource (sops / age / pass)."""

    @abstractmethod
    def seal(self, principal_id: str, name: str, value: str) -> None: ...

    @abstractmethod
    def reveal(self, principal_id: str, name: str) -> str: ...

    @abstractmethod
    def replace(self, principal_id: str, name: str, value: str) -> None:
        """Must not Seal a missing name."""

    @abstractmethod
    def list(self, principal_id: str) -> list[str]:
        """Names only, never values."""

    @abstractmethod
    def delete(self, principal_id: str, name: str) -> None:
        """Must not delete a missing name."""

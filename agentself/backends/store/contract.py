from __future__ import annotations

from abc import ABC, abstractmethod


class StoreError(Exception):
    """Store Resource failure. Must never include a secret value."""


class SecretMissing(StoreError):
    pass


class SecretExists(StoreError):
    pass


class StoreResourceError(StoreError):
    pass


class StoreAccess(ABC):
    """Caller never names the Resource (sops / age / pass)."""

    @abstractmethod
    def create(self, identity_id: str, name: str, value: str) -> None: ...

    @abstractmethod
    def get(self, identity_id: str, name: str) -> str: ...

    @abstractmethod
    def update(self, identity_id: str, name: str, value: str) -> None:
        """Must not create a missing name."""

    @abstractmethod
    def list(self, identity_id: str) -> list[str]:
        """Names only, never values."""

    @abstractmethod
    def delete(self, identity_id: str, name: str) -> None:
        """Must not delete a missing name."""

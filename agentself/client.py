from __future__ import annotations

import builtins
from collections.abc import Callable
from typing import Protocol

from agentself.bind import bind_from_env
from agentself.internal.custody.errors import UnboundCaller
from agentself.internal.log import Log
from agentself.internal.types import (
    BoundCaller,
    EmailConnectView,
    Identity,
    IdentityView,
    MailboxMessage,
    WalletAuthorization,
    WalletBalance,
    WalletMaterialStatus,
    WalletSendResult,
)


class CustodyManager(Protocol):
    def init(self, caller: BoundCaller, store_binding: str = "sops") -> Identity: ...

    def create(self, caller: BoundCaller, name: str, value: str) -> bool: ...

    def get(self, caller: BoundCaller, name: str) -> str: ...

    def update(
        self,
        caller: BoundCaller,
        name: str,
        value: str,
        *,
        unsafe: bool = False,
    ) -> None: ...

    def list(self, caller: BoundCaller) -> builtins.list[str]: ...

    def exists(self, caller: BoundCaller, name: str) -> bool: ...

    def delete(self, caller: BoundCaller, name: str) -> None: ...

    def protected_secret_names(self, caller: BoundCaller) -> builtins.list[str]: ...

    def note_set(self, caller: BoundCaller, name: str, value: str) -> str: ...

    def note_get(self, caller: BoundCaller, name: str) -> str: ...

    def note_list(self, caller: BoundCaller) -> builtins.list[str]: ...

    def note_exists(self, caller: BoundCaller, name: str) -> bool: ...

    def note_delete(self, caller: BoundCaller, name: str) -> None: ...

    def email_send(
        self, caller: BoundCaller, to: str, subject: str, body: str
    ) -> dict[str, str]: ...

    def email_receive(
        self,
        caller: BoundCaller,
        *,
        message_id: str | None = None,
        include_body: bool = True,
    ) -> builtins.list[MailboxMessage]: ...

    def email_list(
        self,
        caller: BoundCaller,
        *,
        status: str | None = None,
        acted: bool | None = None,
        rejected: bool | None = None,
        limit: int | None = None,
    ) -> builtins.list[MailboxMessage]: ...

    def email_find(
        self,
        caller: BoundCaller,
        query: str,
        *,
        status: str | None = None,
        acted: bool | None = None,
        rejected: bool | None = None,
        limit: int | None = None,
    ) -> builtins.list[MailboxMessage]: ...

    def email_mark(
        self,
        caller: BoundCaller,
        message_id: str,
        *,
        acted: bool | None = None,
        rejected: bool = False,
    ) -> bool: ...

    def email_connect(
        self,
        caller: BoundCaller,
        *,
        answers: dict[str, str] | None = None,
        state: str | None = None,
    ) -> EmailConnectView: ...

    def wallet_address(self, caller: BoundCaller) -> str: ...

    def wallet_authorize(self, caller: BoundCaller, message: str) -> str: ...

    def wallet_verify(
        self,
        caller: BoundCaller,
        message: str,
        authorization: str,
    ) -> WalletAuthorization: ...

    def wallet_balance(self, caller: BoundCaller) -> WalletBalance: ...

    def wallet_send(
        self,
        caller: BoundCaller,
        to: str,
        amount: str,
        asset: str = "",
        test: bool = False,
    ) -> WalletSendResult: ...

    def wallet_material_status(self, caller: BoundCaller) -> WalletMaterialStatus: ...

    def identity(self, caller: BoundCaller) -> IdentityView: ...


class Client:
    def __init__(
        self,
        manager: CustodyManager,
        log: Log,
        bind: Callable[[], BoundCaller] | None = None,
    ) -> None:
        self._manager = manager
        self._log = log
        self._bind = bind or bind_from_env

    def init(self, store_binding: str = "sops") -> dict[str, str]:
        caller = self._require_caller()
        identity = self._manager.init(caller, store_binding)
        return identity.public_view()

    def create(self, name: str, value: str) -> bool:
        caller = self._require_caller()
        return self._manager.create(caller, name, value)

    def get(self, name: str) -> str:
        caller = self._require_caller()
        return self._manager.get(caller, name)

    def update(self, name: str, value: str, *, unsafe: bool = False) -> None:
        caller = self._require_caller()
        self._manager.update(caller, name, value, unsafe=unsafe)

    def list(self) -> builtins.list[str]:
        caller = self._require_caller()
        return self._manager.list(caller)

    def exists(self, name: str) -> bool:
        caller = self._require_caller()
        return self._manager.exists(caller, name)

    def delete(self, name: str) -> None:
        caller = self._require_caller()
        self._manager.delete(caller, name)

    def protected_secret_names(self) -> builtins.list[str]:
        caller = self._require_caller()
        return self._manager.protected_secret_names(caller)

    def note_set(self, name: str, value: str) -> str:
        caller = self._require_caller()
        return self._manager.note_set(caller, name, value)

    def note_get(self, name: str) -> str:
        caller = self._require_caller()
        return self._manager.note_get(caller, name)

    def note_list(self) -> builtins.list[str]:
        caller = self._require_caller()
        return self._manager.note_list(caller)

    def note_exists(self, name: str) -> bool:
        caller = self._require_caller()
        return self._manager.note_exists(caller, name)

    def note_delete(self, name: str) -> None:
        caller = self._require_caller()
        self._manager.note_delete(caller, name)

    def email_send(self, to: str, subject: str, body: str) -> dict[str, str]:
        caller = self._require_caller()
        return self._manager.email_send(caller, to, subject, body)

    def email_receive(
        self,
        *,
        message_id: str | None = None,
        include_body: bool = True,
    ) -> builtins.list[MailboxMessage]:
        caller = self._require_caller()
        return self._manager.email_receive(
            caller, message_id=message_id, include_body=include_body
        )

    def email_list(
        self,
        *,
        status: str | None = None,
        acted: bool | None = None,
        rejected: bool | None = None,
        limit: int | None = None,
    ) -> builtins.list[MailboxMessage]:
        caller = self._require_caller()
        return self._manager.email_list(
            caller, status=status, acted=acted, rejected=rejected, limit=limit
        )

    def email_find(
        self,
        query: str,
        *,
        status: str | None = None,
        acted: bool | None = None,
        rejected: bool | None = None,
        limit: int | None = None,
    ) -> builtins.list[MailboxMessage]:
        caller = self._require_caller()
        return self._manager.email_find(
            caller,
            query,
            status=status,
            acted=acted,
            rejected=rejected,
            limit=limit,
        )

    def email_mark(
        self,
        message_id: str,
        *,
        acted: bool | None = None,
        rejected: bool = False,
    ) -> bool:
        caller = self._require_caller()
        return self._manager.email_mark(
            caller, message_id, acted=acted, rejected=rejected
        )

    def email_connect(
        self,
        *,
        answers: dict[str, str] | None = None,
        state: str | None = None,
    ) -> EmailConnectView:
        caller = self._require_caller()
        return self._manager.email_connect(caller, answers=answers, state=state)

    def wallet_address(self) -> str:
        caller = self._require_caller()
        return self._manager.wallet_address(caller)

    def wallet_authorize(self, message: str) -> str:
        caller = self._require_caller()
        return self._manager.wallet_authorize(caller, message)

    def wallet_verify(self, message: str, authorization: str) -> WalletAuthorization:
        caller = self._require_caller()
        return self._manager.wallet_verify(caller, message, authorization)

    def wallet_balance(self) -> WalletBalance:
        caller = self._require_caller()
        return self._manager.wallet_balance(caller)

    def wallet_send(
        self, to: str, amount: str, asset: str = "", *, test: bool = False
    ) -> WalletSendResult:
        caller = self._require_caller()
        return self._manager.wallet_send(caller, to, amount, asset, test=test)

    def wallet_material_status(self) -> WalletMaterialStatus:
        caller = self._require_caller()
        return self._manager.wallet_material_status(caller)

    def identity(self) -> IdentityView:
        caller = self._require_caller()
        return self._manager.identity(caller)

    def _require_caller(self) -> BoundCaller:
        try:
            return self._bind()
        except UnboundCaller:
            self._log.record("bind", None, None, "unbound")
            raise

from __future__ import annotations

import builtins
from collections.abc import Callable
from typing import Protocol

from agentself.bind import bind_from_env
from agentself.host import default_wallet_asset
from agentself.internal.custody.errors import UnboundCaller
from agentself.internal.log import Log
from agentself.internal.types import BoundCaller, Principal


class CustodyManager(Protocol):
    def enroll(self, caller: BoundCaller, store_binding: str = "sops") -> Principal: ...

    def seal(
        self,
        caller: BoundCaller,
        name: str,
        value: str,
        hold_owner: str | None = None,
    ) -> bool: ...

    def reveal(
        self,
        caller: BoundCaller,
        name: str,
        hold_owner: str | None = None,
    ) -> str: ...

    def replace(
        self,
        caller: BoundCaller,
        name: str,
        value: str,
        hold_owner: str | None = None,
    ) -> None: ...

    def list(
        self, caller: BoundCaller, hold_owner: str | None = None
    ) -> builtins.list[str]: ...

    def exists(
        self,
        caller: BoundCaller,
        name: str,
        hold_owner: str | None = None,
    ) -> bool: ...

    def delete(
        self, caller: BoundCaller, name: str, hold_owner: str | None = None
    ) -> None: ...

    def email_send(
        self,
        caller: BoundCaller,
        to: str,
        subject: str,
        body: str,
        hold_owner: str | None = None,
    ) -> None: ...

    def email_recv(
        self,
        caller: BoundCaller,
        hold_owner: str | None = None,
        message_id: str | None = None,
    ) -> builtins.list[dict[str, str]]: ...

    def email_list(
        self, caller: BoundCaller, hold_owner: str | None = None
    ) -> builtins.list[dict[str, str]]: ...

    def email_connect(
        self,
        caller: BoundCaller,
        hold_owner: str | None = None,
        *,
        answers: dict[str, str] | None = None,
        state: str | None = None,
    ) -> dict[str, object]: ...

    def wallet_address(
        self, caller: BoundCaller, hold_owner: str | None = None
    ) -> str: ...

    def wallet_sign(
        self, caller: BoundCaller, message: str, hold_owner: str | None = None
    ) -> str: ...

    def wallet_verify(
        self,
        caller: BoundCaller,
        message: str,
        authorization: str,
        hold_owner: str | None = None,
    ) -> dict[str, object]: ...

    def wallet_balance(
        self, caller: BoundCaller, hold_owner: str | None = None
    ) -> dict[str, str]: ...

    def wallet_send(
        self,
        caller: BoundCaller,
        to: str,
        amount: str,
        asset: str = "USDC",
        hold_owner: str | None = None,
    ) -> None: ...

    def identity(
        self, caller: BoundCaller, hold_owner: str | None = None
    ) -> dict[str, object]: ...


class Gateway:
    def __init__(
        self,
        manager: CustodyManager,
        log: Log,
        bind: Callable[[], BoundCaller] | None = None,
    ) -> None:
        self._manager = manager
        self._log = log
        self._bind = bind or bind_from_env

    def enroll(self, store_binding: str = "sops") -> dict[str, str]:
        caller = self._require_caller()
        principal = self._manager.enroll(caller, store_binding)
        return principal.public_view()

    def seal(self, name: str, value: str, hold_owner: str | None = None) -> bool:
        caller = self._require_caller()
        return self._manager.seal(caller, name, value, hold_owner=hold_owner)

    def reveal(self, name: str, hold_owner: str | None = None) -> str:
        caller = self._require_caller()
        return self._manager.reveal(caller, name, hold_owner=hold_owner)

    def replace(self, name: str, value: str, hold_owner: str | None = None) -> None:
        caller = self._require_caller()
        self._manager.replace(caller, name, value, hold_owner=hold_owner)

    def list(self, hold_owner: str | None = None) -> builtins.list[str]:
        caller = self._require_caller()
        return self._manager.list(caller, hold_owner=hold_owner)

    def exists(self, name: str, hold_owner: str | None = None) -> bool:
        caller = self._require_caller()
        return self._manager.exists(caller, name, hold_owner=hold_owner)

    def delete(self, name: str, hold_owner: str | None = None) -> None:
        caller = self._require_caller()
        self._manager.delete(caller, name, hold_owner=hold_owner)

    def email_send(
        self,
        to: str,
        subject: str,
        body: str,
        hold_owner: str | None = None,
    ) -> None:
        caller = self._require_caller()
        self._manager.email_send(caller, to, subject, body, hold_owner=hold_owner)

    def email_recv(
        self,
        hold_owner: str | None = None,
        *,
        message_id: str | None = None,
    ) -> builtins.list[dict[str, str]]:
        caller = self._require_caller()
        return self._manager.email_recv(
            caller, hold_owner=hold_owner, message_id=message_id
        )

    def email_list(
        self, hold_owner: str | None = None
    ) -> builtins.list[dict[str, str]]:
        caller = self._require_caller()
        return self._manager.email_list(caller, hold_owner=hold_owner)

    def email_connect(
        self,
        hold_owner: str | None = None,
        *,
        answers: dict[str, str] | None = None,
        state: str | None = None,
    ) -> dict[str, object]:
        caller = self._require_caller()
        return self._manager.email_connect(
            caller,
            hold_owner=hold_owner,
            answers=answers,
            state=state,
        )

    def email_receive(
        self,
        hold_owner: str | None = None,
        *,
        message_id: str | None = None,
    ) -> builtins.list[dict[str, str]]:
        return self.email_recv(hold_owner=hold_owner, message_id=message_id)

    def wallet_address(self, hold_owner: str | None = None) -> str:
        caller = self._require_caller()
        return self._manager.wallet_address(caller, hold_owner=hold_owner)

    def wallet_sign(self, message: str, hold_owner: str | None = None) -> str:
        caller = self._require_caller()
        return self._manager.wallet_sign(caller, message, hold_owner=hold_owner)

    def wallet_verify(
        self,
        message: str,
        authorization: str,
        hold_owner: str | None = None,
    ) -> dict[str, object]:
        caller = self._require_caller()
        return self._manager.wallet_verify(
            caller, message, authorization, hold_owner=hold_owner
        )

    def wallet_authorize(self, message: str, hold_owner: str | None = None) -> str:
        return self.wallet_sign(message, hold_owner=hold_owner)

    def wallet_balance(self, hold_owner: str | None = None) -> dict[str, str]:
        caller = self._require_caller()
        return self._manager.wallet_balance(caller, hold_owner=hold_owner)

    def wallet_send(
        self,
        to: str,
        amount: str,
        asset: str = "",
        hold_owner: str | None = None,
    ) -> str:
        caller = self._require_caller()
        wanted = default_wallet_asset(
            getattr(self._manager, "_wallet_backend", ""),
            asset,
        )
        self._manager.wallet_send(caller, to, amount, wanted, hold_owner=hold_owner)
        return wanted

    def identity(self, hold_owner: str | None = None) -> dict[str, object]:
        caller = self._require_caller()
        return self._manager.identity(caller, hold_owner=hold_owner)

    def _require_caller(self) -> BoundCaller:
        try:
            return self._bind()
        except UnboundCaller:
            self._log.record("bind", None, None, "unbound")
            raise

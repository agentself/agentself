from __future__ import annotations

import builtins
from collections.abc import Callable
from typing import Protocol

from agentself.bind import bind_from_env
from agentself.internal.custody.errors import UnboundCaller
from agentself.internal.log import Log
from agentself.internal.types import BoundCaller, Identity


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

    def email_send(
        self, caller: BoundCaller, to: str, subject: str, body: str
    ) -> None: ...

    def email_receive(
        self,
        caller: BoundCaller,
        *,
        message_id: str | None = None,
        include_body: bool = True,
    ) -> builtins.list[dict[str, str]]: ...

    def email_list(self, caller: BoundCaller) -> builtins.list[dict[str, str]]: ...

    def email_connect(
        self,
        caller: BoundCaller,
        *,
        answers: dict[str, str] | None = None,
        state: str | None = None,
    ) -> dict[str, object]: ...

    def wallet_address(self, caller: BoundCaller) -> str: ...

    def wallet_authorize(self, caller: BoundCaller, message: str) -> str: ...

    def wallet_verify(
        self,
        caller: BoundCaller,
        message: str,
        authorization: str,
    ) -> dict[str, object]: ...

    def wallet_balance(self, caller: BoundCaller) -> dict[str, str]: ...

    def wallet_send(
        self,
        caller: BoundCaller,
        to: str,
        amount: str,
        asset: str = "",
    ) -> dict[str, str]: ...

    def wallet_material_status(self, caller: BoundCaller) -> dict[str, object]: ...

    def identity(self, caller: BoundCaller) -> dict[str, object]: ...


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

    def email_send(self, to: str, subject: str, body: str) -> None:
        caller = self._require_caller()
        self._manager.email_send(caller, to, subject, body)

    def email_receive(
        self,
        *,
        message_id: str | None = None,
        include_body: bool = True,
    ) -> builtins.list[dict[str, str]]:
        caller = self._require_caller()
        return self._manager.email_receive(
            caller, message_id=message_id, include_body=include_body
        )

    def email_list(self) -> builtins.list[dict[str, str]]:
        caller = self._require_caller()
        return self._manager.email_list(caller)

    def email_connect(
        self,
        *,
        answers: dict[str, str] | None = None,
        state: str | None = None,
    ) -> dict[str, object]:
        caller = self._require_caller()
        return self._manager.email_connect(caller, answers=answers, state=state)

    def wallet_address(self) -> str:
        caller = self._require_caller()
        return self._manager.wallet_address(caller)

    def wallet_authorize(self, message: str) -> str:
        caller = self._require_caller()
        return self._manager.wallet_authorize(caller, message)

    def wallet_verify(self, message: str, authorization: str) -> dict[str, object]:
        caller = self._require_caller()
        return self._manager.wallet_verify(caller, message, authorization)

    def wallet_balance(self) -> dict[str, str]:
        caller = self._require_caller()
        return self._manager.wallet_balance(caller)

    def wallet_send(self, to: str, amount: str, asset: str = "") -> dict[str, str]:
        caller = self._require_caller()
        return self._manager.wallet_send(caller, to, amount, asset)

    def wallet_material_status(self) -> dict[str, object]:
        caller = self._require_caller()
        return self._manager.wallet_material_status(caller)

    def identity(self) -> dict[str, object]:
        caller = self._require_caller()
        return self._manager.identity(caller)

    def _require_caller(self) -> BoundCaller:
        try:
            return self._bind()
        except UnboundCaller:
            self._log.record("bind", None, None, "unbound")
            raise

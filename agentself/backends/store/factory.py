from __future__ import annotations

from pathlib import Path

from agentself.backends.store.contract import StoreAccess, StoreResourceError
from agentself.internal.log import Log


class StoreAccessFactory:
    def __init__(self, vault_root: Path, log: Log) -> None:
        self._root = Path(vault_root)
        self._log = log

    def for_binding(self, binding: str) -> StoreAccess:
        if binding == "sops":
            from agentself.backends.store.sops import SopsStoreAccess

            return SopsStoreAccess(self._root, self._log)
        if binding == "pass":
            from agentself.backends.store.passstore import PassStoreAccess

            return PassStoreAccess(self._root, self._log)
        raise StoreResourceError("unknown store binding")

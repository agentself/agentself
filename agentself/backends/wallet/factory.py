from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agentself.backends.wallet.contract import WalletAccess, WalletError

if TYPE_CHECKING:
    from agentself.backends.wallet.rpc import RpcClient

from agentself.internal.log import Log


class WalletAccessFactory:
    """No failover."""

    def __init__(
        self,
        log: Log,
        *,
        rpc: RpcClient | None = None,
        eth_rpc_url: str = "",
        vault_root: str | Path | None = None,
        rpc_opener=None,
    ) -> None:
        self._log = log
        self._rpc = rpc
        self._eth_rpc_url = (eth_rpc_url or "").strip()
        self._root = Path(vault_root) if vault_root is not None else None
        self._rpc_opener = rpc_opener

    def for_binding(self, binding: str) -> WalletAccess:
        if binding == "base":
            from agentself.backends.wallet.base import BaseWalletAccess

            return BaseWalletAccess(
                self._log,
                rpc=self._rpc,
                rpc_url=self._eth_rpc_url or None,
                rpc_opener=self._rpc_opener,
                vault_root=self._root,
            )
        if binding == "ethereum":
            from agentself.backends.wallet.ethereum import EthereumWalletAccess

            return EthereumWalletAccess(
                self._log,
                rpc=self._rpc,
                rpc_url=self._eth_rpc_url,
                rpc_opener=self._rpc_opener,
                vault_root=self._root,
            )
        raise WalletError("unknown wallet binding")

from __future__ import annotations

from agentself.backends.wallet.base.errors import NoEthForGas
from agentself.backends.wallet.chain import ChainWalletAccess
from agentself.backends.wallet.contract import CannotSend


class BaseWalletAccess(ChainWalletAccess):
    chain_name = "base"
    chain_label = "Base"
    chain_id = 8453
    default_rpc = "https://mainnet.base.org"
    fallback_rpcs = (
        "https://base.publicnode.com",
        "https://base.drpc.org",
    )
    usdc = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

    def send(self, identity_id: str, to: str, amount: str, asset: str) -> str:
        try:
            return super().send(identity_id, to, amount, asset)
        except CannotSend as exc:
            if exc.reason == "no_gas" and not isinstance(exc, NoEthForGas):
                raise NoEthForGas() from None
            raise


__all__ = ["BaseWalletAccess", "NoEthForGas"]

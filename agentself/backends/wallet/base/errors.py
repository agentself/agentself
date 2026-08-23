from __future__ import annotations

from agentself.backends.wallet.contract import CannotSend


class NoEthForGas(CannotSend):
    def __init__(self) -> None:
        super().__init__("need ETH for gas", reason="no_gas")

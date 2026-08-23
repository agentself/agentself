from __future__ import annotations

from agentself.backends.wallet.contract import CannotSend


class NoEthForGas(CannotSend):
    """This backend's send failure, not the contract."""

    def __init__(self) -> None:
        super().__init__("need ETH for gas")

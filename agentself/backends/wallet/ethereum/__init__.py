from __future__ import annotations

from agentself.backends.wallet.chain import ChainWalletAccess


class EthereumWalletAccess(ChainWalletAccess):
    """Operator supplies a public RPC."""

    chain_name = "ethereum"
    chain_label = "Ethereum"
    chain_id = 1
    default_rpc = ""
    usdc = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

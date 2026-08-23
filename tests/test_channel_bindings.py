"""Two classes per channel constructed by bind swap."""

from __future__ import annotations

import pytest

from agentself.backends.email.contract import MailboxError
from agentself.backends.email.factory import MailboxAccessFactory
from agentself.backends.store.contract import StoreResourceError
from agentself.backends.store.factory import StoreAccessFactory
from agentself.backends.wallet.base import BaseWalletAccess
from agentself.backends.wallet.contract import WalletError
from agentself.backends.wallet.ethereum import EthereumWalletAccess
from agentself.backends.wallet.factory import WalletAccessFactory
from agentself.internal.log import MemoryLog

from tests.support import MockRpc


def test_unknown_binding_fails_closed_no_failover(vault):
    log = MemoryLog()
    with pytest.raises(StoreResourceError, match="unknown store binding"):
        StoreAccessFactory(vault, log).for_binding("openbao")
    with pytest.raises(MailboxError, match="unknown mailbox binding"):
        MailboxAccessFactory(vault, log).for_binding("resend")
    with pytest.raises(WalletError, match="unknown wallet binding"):
        WalletAccessFactory(log).for_binding("cloudflare")


def test_wallet_binds_select_distinct_chains():
    log = MemoryLog()
    factory = WalletAccessFactory(log, rpc=MockRpc())
    base = factory.for_binding("base")
    ethereum = factory.for_binding("ethereum")
    assert isinstance(base, BaseWalletAccess)
    assert isinstance(ethereum, EthereumWalletAccess)
    assert base.chain_id == 8453
    assert ethereum.chain_id == 1

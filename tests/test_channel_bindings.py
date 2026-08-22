"""Two classes per channel constructed by bind swap."""

from __future__ import annotations

import pytest

from agentself.backends.email.agentmail import AgentMailMailboxAccess
from agentself.backends.email.contract import MailboxAccess, MailboxError
from agentself.backends.email.factory import MailboxAccessFactory
from agentself.backends.email.imap import ImapMailboxAccess
from agentself.backends.store.contract import StoreAccess, StoreResourceError
from agentself.backends.store.factory import StoreAccessFactory
from agentself.backends.store.passstore import PassStoreAccess
from agentself.backends.store.sops import SopsStoreAccess
from agentself.backends.wallet.base import BaseWalletAccess
from agentself.backends.wallet.contract import WalletAccess, WalletError
from agentself.backends.wallet.ethereum import EthereumWalletAccess
from agentself.backends.wallet.factory import WalletAccessFactory
from agentself.internal.log import MemoryLog

from tests.maildir_mailbox import MaildirMailboxAccess
from tests.support import DoubleMailboxFactory, MockRpc


def test_two_store_classes_by_bind_swap(vault):
    log = MemoryLog()
    factory = StoreAccessFactory(vault, log)
    a = factory.for_binding("sops")
    b = factory.for_binding("pass")
    assert type(a) is not type(b)
    assert isinstance(a, StoreAccess)
    assert isinstance(b, StoreAccess)
    assert isinstance(a, SopsStoreAccess)
    assert isinstance(b, PassStoreAccess)


def test_unknown_binding_fails_closed_no_failover(vault):
    log = MemoryLog()
    with pytest.raises(StoreResourceError, match="unknown store binding"):
        StoreAccessFactory(vault, log).for_binding("openbao")
    with pytest.raises(MailboxError, match="unknown mailbox binding"):
        MailboxAccessFactory(vault, log).for_binding("resend")
    with pytest.raises(WalletError, match="unknown wallet binding"):
        WalletAccessFactory(log).for_binding("cloudflare")


def test_two_mailbox_classes_by_bind_swap(vault):
    log = MemoryLog()
    factory = DoubleMailboxFactory(
        MailboxAccessFactory(vault, log, domain=""), vault, log
    )
    a = factory.for_binding("maildir")
    c = factory.for_binding("agentmail")
    d = factory.for_binding("imap")
    assert type(c) is not type(a)
    assert type(d) is not type(a)
    assert type(d) is not type(c)
    assert isinstance(a, MailboxAccess)
    assert isinstance(c, MailboxAccess)
    assert isinstance(d, MailboxAccess)
    assert isinstance(a, MaildirMailboxAccess)
    assert isinstance(c, AgentMailMailboxAccess)
    assert isinstance(d, ImapMailboxAccess)


def test_two_wallet_classes_by_bind_swap():
    log = MemoryLog()
    factory = WalletAccessFactory(log, rpc=MockRpc())
    a = factory.for_binding("base")
    c = factory.for_binding("ethereum")
    assert type(a) is not type(c)
    assert isinstance(a, WalletAccess)
    assert isinstance(c, WalletAccess)
    assert isinstance(a, BaseWalletAccess)
    assert isinstance(c, EthereumWalletAccess)
    assert a.chain_id == 8453
    assert c.chain_id == 1

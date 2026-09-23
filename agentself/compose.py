from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentself.backends.email.factory import MailboxAccessFactory
from agentself.backends.store.factory import StoreAccessFactory
from agentself.backends.wallet.factory import WalletAccessFactory
from agentself.client import Client
from agentself.host import (
    CHANNELS,
    ENV_ETH_RPC_URL,
    ENV_IMAP_HOST,
    ENV_IMAP_PORT,
    ENV_MAIL_DOMAIN,
    ENV_MAIL_HOST,
    ENV_MAIL_USER,
    ENV_SMTP_HOST,
    ENV_SMTP_PORT,
    UnknownBind,
)
from agentself.internal.custody.manager import CustodyManager
from agentself.internal.files import ensure_private_dir
from agentself.internal.log import Log, StreamLog
from agentself.internal.registry import FileIdentityAccess
from agentself.internal.types import BoundCaller
from agentself.local import default_identity_dir, read_config, setting_from

if TYPE_CHECKING:
    from agentself.backends.wallet.rpc import RpcClient


def compose(
    vault_root: str | Path | None = None,
    *,
    log: Log | None = None,
    bind: Callable[[], BoundCaller] | None = None,
    email_backend: str | None = None,
    wallet_backend: str | None = None,
    mail_domain: str | None = None,
    rpc: RpcClient | None = None,
    eth_rpc_url: str | None = None,
    rpc_opener: Any = None,
) -> Client:
    if vault_root is None:
        vault_root = default_identity_dir()
    log = log or StreamLog()
    root = ensure_private_dir(Path(vault_root))
    # Settings for this client come from one read. for_binding still waits
    # until an operation uses that channel.
    snapshot = read_config(root)
    allowed = frozenset(CHANNELS["store"].names)
    identities = FileIdentityAccess(root, log, allowed_bindings=allowed)
    stores = StoreAccessFactory(root, log)
    domain = (
        setting_from(snapshot, "mail_domain", ENV_MAIL_DOMAIN)
        if mail_domain is None
        else mail_domain
    )
    mail_settings = {
        "mail_domain": domain,
        "mail_host": setting_from(snapshot, "mail_host", ENV_MAIL_HOST),
        "imap_host": setting_from(snapshot, "imap_host", ENV_IMAP_HOST),
        "smtp_host": setting_from(snapshot, "smtp_host", ENV_SMTP_HOST),
        "imap_port": setting_from(snapshot, "imap_port", ENV_IMAP_PORT),
        "smtp_port": setting_from(snapshot, "smtp_port", ENV_SMTP_PORT),
        "mail_user": setting_from(snapshot, "mail_user", ENV_MAIL_USER),
    }
    rpc_url = (
        os.environ.get(ENV_ETH_RPC_URL, "") if eth_rpc_url is None else eth_rpc_url
    )
    mailboxes = MailboxAccessFactory(root, log, domain=domain, settings=mail_settings)
    wallets = WalletAccessFactory(
        log,
        rpc=rpc,
        eth_rpc_url=rpc_url,
        vault_root=root,
        rpc_opener=rpc_opener,
    )
    manager = CustodyManager(
        identities,
        stores,
        log,
        mailboxes=mailboxes,
        wallets=wallets,
        email_backend=_resolved_backend(snapshot, "email", email_backend),
        wallet_backend=_resolved_backend(snapshot, "wallet", wallet_backend),
        allowed_store_bindings=allowed,
        vault_root=root,
    )
    return Client(manager, log, bind=bind, vault=root, snapshot=snapshot)


def _resolved_backend(snapshot, channel: str, explicit: str | None) -> str:
    spec = CHANNELS[channel]
    value = setting_from(
        snapshot,
        spec.config_key or f"{channel}_backend",
        spec.env or "",
        spec.default,
        explicit,
    )
    if value not in spec.names:
        raise UnknownBind(channel, value)
    return value

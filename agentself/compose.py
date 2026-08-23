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
    unknown_bind,
)
from agentself.internal.custody.manager import CustodyManager
from agentself.internal.files import ensure_private_dir
from agentself.internal.log import Log, StreamLog
from agentself.internal.registry import FileIdentityAccess
from agentself.internal.types import BoundCaller
from agentself.local import default_identity_dir, resolve_setting

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
    identities = FileIdentityAccess(root, log)
    stores = StoreAccessFactory(root, log)
    domain = (
        mail_domain
        if mail_domain is not None
        else resolve_setting(root, "mail_domain", ENV_MAIL_DOMAIN)
    )
    mailboxes = MailboxAccessFactory(
        root,
        log,
        domain=domain,
        mail_host=resolve_setting(root, "mail_host", ENV_MAIL_HOST),
        imap_host=resolve_setting(root, "imap_host", ENV_IMAP_HOST),
        smtp_host=resolve_setting(root, "smtp_host", ENV_SMTP_HOST),
        imap_port=resolve_setting(root, "imap_port", ENV_IMAP_PORT),
        smtp_port=resolve_setting(root, "smtp_port", ENV_SMTP_PORT),
        mail_user=resolve_setting(root, "mail_user", ENV_MAIL_USER),
    )
    wallets = WalletAccessFactory(
        log,
        rpc=rpc,
        eth_rpc_url=(
            eth_rpc_url
            if eth_rpc_url is not None
            else os.environ.get(ENV_ETH_RPC_URL, "")
        ),
        vault_root=root,
        rpc_opener=rpc_opener,
    )
    manager = CustodyManager(
        identities,
        stores,
        log,
        mailboxes=mailboxes,
        wallets=wallets,
        email_backend=_resolved_backend(root, "email", email_backend),
        wallet_backend=_resolved_backend(root, "wallet", wallet_backend),
    )
    return Client(manager, log, bind=bind)


def _resolved_backend(root: Path, channel: str, explicit: str | None) -> str:
    spec = CHANNELS[channel]
    value = resolve_setting(
        root,
        spec.config_key or f"{channel}_backend",
        spec.env or "",
        spec.default,
        explicit,
    )
    if unknown_bind(channel, value):
        raise UnknownBind(channel, value)
    return value

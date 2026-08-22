from __future__ import annotations

from pathlib import Path

from agentself.backends.email.contract import MailboxAccess, MailboxError
from agentself.internal.log import Log


class MailboxAccessFactory:
    def __init__(
        self,
        vault_root: Path,
        log: Log,
        *,
        domain: str = "",
        mail_host: str = "",
        imap_host: str = "",
        smtp_host: str = "",
        imap_port: str = "",
        smtp_port: str = "",
        mail_user: str = "",
    ) -> None:
        self._root = Path(vault_root)
        self._log = log
        self._domain = (domain or "").strip()
        self._mail_host = (mail_host or "").strip()
        self._imap_host = (imap_host or "").strip()
        self._smtp_host = (smtp_host or "").strip()
        self._imap_port = (imap_port or "").strip()
        self._smtp_port = (smtp_port or "").strip()
        self._mail_user = (mail_user or "").strip()

    def for_binding(self, binding: str) -> MailboxAccess:
        if binding == "agentmail":
            from agentself.backends.email.agentmail import (
                AgentMailMailboxAccess,
            )

            return AgentMailMailboxAccess(self._root, self._log, domain=self._domain)
        if binding == "imap":
            from agentself.backends.email.imap import ImapMailboxAccess

            return ImapMailboxAccess(
                self._root,
                self._log,
                domain=self._domain,
                mail_host=self._mail_host,
                imap_host=self._imap_host,
                smtp_host=self._smtp_host,
                imap_port=self._imap_port,
                smtp_port=self._smtp_port,
                mail_user=self._mail_user,
            )
        raise MailboxError("unknown mailbox binding")

from __future__ import annotations

from collections.abc import Mapping
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
        settings: Mapping[str, str] | None = None,
    ) -> None:
        self._root = Path(vault_root)
        self._log = log
        self._settings = {
            str(key): "" if value is None else str(value)
            for key, value in (settings or {}).items()
        }
        self._domain = (domain or self._settings.get("mail_domain") or "").strip()

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
                settings=self._settings,
            )
        raise MailboxError("unknown mailbox binding")

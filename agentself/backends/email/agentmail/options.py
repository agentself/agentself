from __future__ import annotations

from agentself.internal.names import EMAIL_ADDRESS_NAME, EMAIL_CREDENTIAL_NAME
from agentself.internal.setup import (
    SetupAction,
    address_option,
    credential_option,
)

SOURCE_AGENTMAIL_CREDENTIAL = "AGENTSELF_AGENTMAIL_API_KEY"

HELP_AGENTMAIL_CREDENTIAL = (
    "AgentMail API key (starts with am_). Use an existing approved key. If none is "
    "available, open API Keys at https://console.agentmail.to and wait for the operator "
    "to create and copy one. Creating a new AgentMail "
    "organization through signup is an external account action and requires explicit "
    "user authorization. When that exact action is authorized for a first-time, "
    "unclaimed signup, follow https://docs.agentmail.to/api-reference/agent/sign-up "
    "once with the approved email identity and capture the key from its HTTP response. "
    "If signup reports that the identity is claimed, forbidden, or unavailable, stop "
    "and ask the user; do not probe aliases or disposable email providers. The OTP or "
    "confirmation email does not contain the key. A key is shown once and cannot be "
    "retrieved; if lost, create another in the console. Stop credential discovery as "
    "soon as one key validates. For transient use, provide "
    "AGENTSELF_EMAIL_CREDENTIAL or AGENTSELF_AGENTMAIL_API_KEY on every email "
    "invocation. To store it in this identity, write the key to a file and continue "
    "with --result-file. Cannot obtain a key: agentself init --force --email imap, "
    "or stop."
)
HELP_AGENTMAIL_ADDRESS = (
    "Inbox address when this key owns more than one. Use an address the provider "
    "listed. Do not invent one."
)

_API_KEYS_ACTION: SetupAction = {
    "kind": "open_url",
    "label": "Open AgentMail API Keys",
    "url": "https://console.agentmail.to",
}

OPTIONS = (
    credential_option(
        required=True,
        source=SOURCE_AGENTMAIL_CREDENTIAL,
        help=HELP_AGENTMAIL_CREDENTIAL,
        prompt="Paste the API key",
        persist=True,
        persist_as=EMAIL_CREDENTIAL_NAME,
        action=_API_KEYS_ACTION,
    ),
    address_option(
        required=False,
        help=HELP_AGENTMAIL_ADDRESS,
        persist=True,
        persist_as=EMAIL_ADDRESS_NAME,
    ),
)


def option_named(name: str, **updates: object) -> dict[str, object]:
    for item in OPTIONS:
        if item.get("name") == name:
            option = dict(item)
            option.update(updates)
            return option
    raise KeyError(name)

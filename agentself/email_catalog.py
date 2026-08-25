"""Neutral setup descriptors for the shipped email bindings.

The host catalog and email adapters both consume this metadata. Keeping it
outside ``agentself.backends`` lets discovery and CLI help describe bindings
without importing an adapter or provider/runtime dependencies.
"""

from __future__ import annotations

from agentself.internal.names import EMAIL_ADDRESS_NAME, EMAIL_CREDENTIAL_NAME
from agentself.internal.setup import (
    SetupAction,
    address_option,
    credential_option,
    setup_option,
)

SOURCE_AGENTMAIL_CREDENTIAL = "AGENTSELF_AGENTMAIL_API_KEY"

HELP_AGENTMAIL_SETUP_METHOD = (
    "Choose existing_credential to connect an approved API key, or create_account "
    "only when the user explicitly authorizes creating a new external AgentMail "
    "account. Account creation sends a verification code to the approved human email."
)
HELP_AGENTMAIL_CREDENTIAL = (
    "Existing AgentMail API key (starts with am_). Open API Keys at "
    "https://console.agentmail.to if an operator needs to create one. For transient "
    "use, provide "
    "AGENTSELF_EMAIL_CREDENTIAL or AGENTSELF_AGENTMAIL_API_KEY on every email "
    "invocation. To store it in this identity, write the key to a file and continue "
    "with --result-file."
)
HELP_AGENTMAIL_HUMAN_EMAIL = (
    "Human email approved for this authorized account creation. A six-digit "
    "verification code will be sent there. If signup says the identity is claimed, "
    "forbidden, or unavailable, stop and ask the user to use existing_credential; "
    "aliases are not tried."
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

AGENTMAIL_OPTIONS = (
    setup_option(
        name="setup_method",
        type="choice",
        required=True,
        choices=("existing_credential", "create_account"),
        help=HELP_AGENTMAIL_SETUP_METHOD,
        runtime_only=True,
    ),
    credential_option(
        required=True,
        source=SOURCE_AGENTMAIL_CREDENTIAL,
        help=HELP_AGENTMAIL_CREDENTIAL,
        persist=True,
        persist_as=EMAIL_CREDENTIAL_NAME,
        action=_API_KEYS_ACTION,
    ),
    setup_option(
        name="human_email",
        type="string",
        required=True,
        help=HELP_AGENTMAIL_HUMAN_EMAIL,
        runtime_only=True,
    ),
    setup_option(
        name="otp",
        type="secret",
        required=True,
        sensitive=True,
        help="Six-digit verification code sent to the approved human email.",
        runtime_only=True,
    ),
    address_option(
        required=False,
        help=HELP_AGENTMAIL_ADDRESS,
        persist=True,
        persist_as=EMAIL_ADDRESS_NAME,
    ),
)

SOURCE_IMAP_CREDENTIAL = "AGENTSELF_MAIL_PASSWORD"
SOURCE_IMAP_MAIL_HOST = "AGENTSELF_MAIL_HOST"
SOURCE_IMAP_IMAP_HOST = "AGENTSELF_IMAP_HOST"
SOURCE_IMAP_SMTP_HOST = "AGENTSELF_SMTP_HOST"

HELP_IMAP_ADDRESS = "Existing mailbox address (user@domain). Do not invent an address."
HELP_IMAP_CREDENTIAL = (
    "Password or app password for that mailbox. Gmail and Outlook need an app "
    "password, not the login password. Env AGENTSELF_MAIL_PASSWORD. Write it to a "
    "file and continue with --result-file."
)
HELP_IMAP_MAIL_HOST = "Shared mail host when IMAP and SMTP use the same hostname."
HELP_IMAP_IMAP_HOST = "IMAP host override. Default is imap.<address-domain>."
HELP_IMAP_SMTP_HOST = "SMTP host override. Default is smtp.<address-domain>."

IMAP_OPTIONS = (
    address_option(
        required=True,
        help=HELP_IMAP_ADDRESS,
        persist=True,
        persist_as=EMAIL_ADDRESS_NAME,
    ),
    credential_option(
        required=True,
        source=SOURCE_IMAP_CREDENTIAL,
        help=HELP_IMAP_CREDENTIAL,
        persist=True,
        persist_as=EMAIL_CREDENTIAL_NAME,
    ),
    setup_option(
        name="mail_host",
        type="string",
        source=SOURCE_IMAP_MAIL_HOST,
        help=HELP_IMAP_MAIL_HOST,
    ),
    setup_option(
        name="imap_host",
        type="string",
        source=SOURCE_IMAP_IMAP_HOST,
        help=HELP_IMAP_IMAP_HOST,
    ),
    setup_option(
        name="smtp_host",
        type="string",
        source=SOURCE_IMAP_SMTP_HOST,
        help=HELP_IMAP_SMTP_HOST,
    ),
)

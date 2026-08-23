from __future__ import annotations

from agentself.internal.names import EMAIL_ADDRESS_NAME, EMAIL_CREDENTIAL_NAME
from agentself.internal.setup import address_option, credential_option, setup_option

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

OPTIONS = (
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

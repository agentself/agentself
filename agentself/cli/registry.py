from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from agentself.host import CHANNELS

Configure = Callable[[argparse.ArgumentParser], None]


@dataclass(frozen=True)
class CommandSpec:
    path: tuple[str, ...]
    summary: str
    handler: str | None
    configure: Configure | None = None
    raw: bool = False
    args: tuple[str, ...] | None = None
    next: str | None = None
    description: str | None = None
    epilog: str | None = None
    dest: str | None = None


def configure_init(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--id",
        dest="identity_id",
        default="",
        metavar="NAME",
        help="Name this identity (default agent)",
    )
    for spec in CHANNELS.values():
        extra = (
            {"choices": spec.names, "default": spec.default}
            if spec.env is None
            else {"default": ""}
        )
        parser.add_argument(
            spec.flag,
            dest=spec.name,
            help=spec.flag_help(),
            **extra,
        )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow identity or backend changes on an existing identity",
    )
    parser.add_argument(
        "--wallet-key-file",
        dest="wallet_key_file",
        default="",
        metavar="PATH",
        help="Seal this hex key as wallet.key on first init. Use - to read stdin",
    )
    parser.add_argument(
        "--unsafe",
        action="store_true",
        help="Allow replacing wallet.key on an existing identity",
    )


def configure_backends(parser: argparse.ArgumentParser) -> None:
    names = tuple(CHANNELS)
    parser.add_argument(
        "channel",
        nargs="?",
        metavar="{" + ",".join(names) + "}",
        choices=names,
        help="One channel. Omit to list all",
    )
    parser.add_argument(
        "backend",
        nargs="?",
        metavar="BACKEND",
        default="",
        help="One backend. Omit to list the channel without option essays",
    )


def _add_secret_write_args(
    parser: argparse.ArgumentParser, *, name_required: bool = True
) -> None:
    parser.add_argument(
        "name",
        nargs=None if name_required else "?",
        metavar="NAME",
        help="Secret name",
    )
    parser.add_argument(
        "value",
        nargs="?",
        metavar="VALUE",
        help="Secret value. Omit when using --file PATH",
    )
    parser.add_argument(
        "--file",
        dest="from_file",
        default="",
        metavar="PATH",
        help="Read the value from a file. Use - to read stdin. A leading UTF-8 BOM is dropped",
    )


def configure_secret_create(parser: argparse.ArgumentParser) -> None:
    _add_secret_write_args(parser, name_required=False)
    parser.add_argument(
        "--from-dir",
        dest="from_dir",
        default="",
        metavar="DIR",
        help="Import each regular file in DIR; basename is the secret name",
    )
    parser.add_argument(
        "--from-files",
        dest="from_files",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Import NAME from PATH. Repeat. Same file decode as --file",
    )
    parser.add_argument(
        "--unsafe",
        action="store_true",
        help="Allow creating wallet.key",
    )


def configure_secret_get(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("name", metavar="NAME", help="Secret name")
    secret_output = parser.add_mutually_exclusive_group()
    secret_output.add_argument(
        "--file",
        dest="to_file",
        default="",
        metavar="PATH",
        help="Write the value to a file instead of stdout",
    )
    secret_output.add_argument(
        "--meta",
        action="store_true",
        help="Print size and SHA-256 without the value",
    )
    parser.add_argument(
        "--unsafe",
        action="store_true",
        help="Allow a raw export of a protected secret",
    )


def configure_secret_update(parser: argparse.ArgumentParser) -> None:
    _add_secret_write_args(parser)
    parser.add_argument(
        "--unsafe",
        action="store_true",
        help="Allow replacing a protected secret",
    )


def configure_named(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("name", metavar="NAME", help="Name")


def _add_note_write_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("name", metavar="NAME", help="Note name")
    parser.add_argument(
        "value",
        nargs="?",
        metavar="VALUE",
        help="Non-secret note text",
    )
    parser.add_argument(
        "--file",
        dest="from_file",
        default="",
        metavar="PATH",
        help="Read exact UTF-8 text from a file or -. Drops a leading BOM",
    )


def configure_note_set(parser: argparse.ArgumentParser) -> None:
    _add_note_write_args(parser)


def configure_email_connect(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--continue",
        dest="do_continue",
        action="store_true",
        help="Continue a pending setup with --state",
    )
    parser.add_argument(
        "--state",
        dest="setup_state",
        default="",
        metavar="STATE",
        help="Opaque state from the previous setup response",
    )
    parser.add_argument(
        "--result-file",
        dest="result_file",
        default="",
        metavar="PATH",
        help="Read the current setup answer from a file. Use - to read stdin",
    )


def configure_email_send(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("to", metavar="TO", help="Recipient address")
    parser.add_argument("subject", metavar="SUBJECT", help="Subject")
    parser.add_argument("body", metavar="BODY", help="Body")


def configure_email_receive(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "message_id",
        nargs="?",
        metavar="REF_OR_ID",
        help="Fetch this stored short ref or provider id again",
    )
    parser.add_argument(
        "--file",
        dest="body_file",
        default="",
        metavar="PATH",
        help="Write one message body to a private file; requires a ref or id",
    )


def _add_mail_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--status",
        choices=("new", "seen"),
        default=None,
        help="Keep messages with this backend read state",
    )
    acted_filter = parser.add_mutually_exclusive_group()
    acted_filter.add_argument(
        "--acted",
        dest="acted_filter",
        action="store_const",
        const=True,
        default=None,
        help="Keep messages marked acted",
    )
    acted_filter.add_argument(
        "--unacted",
        dest="acted_filter",
        action="store_const",
        const=False,
        help="Keep messages not marked acted",
    )


def configure_email_list(parser: argparse.ArgumentParser) -> None:
    _add_mail_filters(parser)


def configure_email_find(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("query", metavar="QUERY", help="Non-empty header substring")
    _add_mail_filters(parser)


def configure_email_mark(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "message_id",
        metavar="REF_OR_ID",
        help="Stored short ref or provider message ID",
    )
    parser.add_argument(
        "mark_state",
        choices=("acted", "unacted"),
        help="Local task state",
    )


def configure_wallet_authorize(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "message",
        nargs="?",
        metavar="MESSAGE",
        help="Action or message to authorize. Omit when using --file PATH",
    )
    parser.add_argument(
        "--file",
        dest="from_file",
        default="",
        metavar="PATH",
        help="Read the message from a file. Use - to read stdin",
    )


def configure_wallet_verify(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "message",
        nargs="?",
        metavar="MESSAGE",
        help="Message that was authorized. Omit when using --file; then AUTHORIZATION is the first positional",
    )
    parser.add_argument(
        "authorization",
        nargs="?",
        metavar="AUTHORIZATION",
        help="Authorization to check",
    )
    parser.add_argument(
        "--file",
        dest="from_file",
        default="",
        metavar="PATH",
        help="Read the message from a file. Use - to read stdin",
    )


def configure_wallet_send(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("to", metavar="TO", help="Destination id")
    parser.add_argument("amount", metavar="AMOUNT", help="Amount to send")
    parser.add_argument(
        "asset",
        nargs="?",
        default="",
        metavar="ASSET",
        help="Asset to send. Omit to use the backend default",
    )


def configure_backup(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", metavar="PATH", help="Destination directory")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace a non-empty destination",
    )


def configure_restore(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", metavar="PATH", help="Source directory")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace a non-empty identity directory",
    )


def configure_install(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--skills",
        nargs="?",
        const="claude",
        default=None,
        metavar="TARGET",
        help="Copy the skill: claude (default), or agents",
    )
    parser.add_argument(
        "--tools",
        action="store_true",
        help="Fetch pinned host tools into the host tools dir",
    )
    parser.add_argument(
        "-g",
        "--global",
        dest="global_install",
        action="store_true",
        help="Install skills under the user home directory",
    )


_H = "agentself.cli.commands"
COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        ("init",),
        "Create the local identity",
        f"{_H}.identity:init_identity",
        configure_init,
        args=("--id", "--wallet", "--email", "--store", "--force", "--wallet-key-file"),
        next="agentself init",
        description=(
            "Create the local identity. Email is optional and does not block init. "
            "Needs host tools on PATH (agentself install --tools)."
        ),
        epilog="Examples:\n  agentself init\n  agentself init --id NAME\n  agentself init --wallet-key-file -",
    ),
    CommandSpec(
        ("show",),
        "Print the current identity",
        f"{_H}.identity:show_identity",
        args=(),
        next="agentself show",
        epilog="Examples:\n  agentself show",
    ),
    CommandSpec(
        ("backends",),
        "List shipped backends",
        f"{_H}.catalog:list_backends",
        configure_backends,
        args=("CHANNEL", "BACKEND"),
        next="agentself backends",
        description=(
            "List shipped host backends. Same command tree on every backend. "
            "Drill in with CHANNEL or CHANNEL BACKEND for options."
        ),
        epilog="Examples:\n  agentself backends\n  agentself backends email agentmail",
    ),
    CommandSpec(
        ("commands",),
        "List featured verbs",
        f"{_H}.catalog:list_commands",
        args=(),
        next="agentself commands",
    ),
    CommandSpec(
        ("diagnose",),
        "Check that this host can run",
        f"{_H}.identity:diagnose_host",
        args=(),
        next="agentself diagnose",
        description="Check Python, host tools, and current backends. Does not fetch tools.",
    ),
    CommandSpec(
        ("secret",),
        "Named secrets",
        None,
        args=("create", "get", "update", "list", "delete", "exists"),
        next="agentself secret list",
        dest="secret_command",
        description=(
            "Named secrets. list prints names only. "
            "wallet.key cannot be deleted and needs --unsafe to export or replace."
        ),
        epilog="Examples:\n  agentself secret create NAME VALUE\n  agentself secret get NAME --raw",
    ),
    CommandSpec(
        ("secret", "create"),
        "Write a named secret. Refuses if the name exists",
        f"{_H}.secret:create_secret",
        configure_secret_create,
        description="VALUE may be omitted when using --file PATH. Use --file - to read stdin.",
        epilog="Examples:\n  agentself secret create NAME VALUE\n  agentself secret create NAME --file -",
    ),
    CommandSpec(
        ("secret", "get"),
        "Export a named secret safely",
        f"{_H}.secret:get_secret",
        configure_secret_get,
        raw=True,
        description=(
            "Default JSON includes name and value. --raw writes exact stored bytes. "
            "wallet.key also requires --unsafe."
        ),
        epilog="Examples:\n  agentself secret get NAME\n  agentself secret get NAME --raw",
    ),
    CommandSpec(
        ("secret", "update"),
        "Update a named secret. The name must exist",
        f"{_H}.secret:update_secret",
        configure_secret_update,
        epilog="Examples:\n  agentself secret update NAME VALUE\n  agentself secret update NAME --file -",
    ),
    CommandSpec(
        ("secret", "list"),
        "List secret names. Never print values",
        f"{_H}.secret:list_secrets",
        args=(),
        next="agentself secret list",
    ),
    CommandSpec(
        ("secret", "delete"),
        "Delete a named secret. wallet.key is protected",
        f"{_H}.secret:delete_secret",
        configure_named,
        description="Delete a named secret. No prompt. wallet.key cannot be deleted.",
    ),
    CommandSpec(
        ("secret", "exists"),
        "Check that a named secret exists",
        f"{_H}.secret:secret_exists",
        configure_named,
    ),
    CommandSpec(
        ("note",),
        "Non-secret identity notes for agent handoff",
        None,
        args=("set", "get", "list", "delete", "exists"),
        next="agentself note list",
        dest="note_command",
        description=(
            "Non-secret identity notes for printable agent handoff context. "
            "Never store credentials, OTPs, private keys, secret values, or mail bodies."
        ),
        epilog="Examples:\n  agentself note set handoff --file PATH\n  agentself note get handoff --raw",
    ),
    CommandSpec(
        ("note", "set"),
        "Create or replace a non-secret note",
        f"{_H}.note:set_note",
        configure_note_set,
        description="Provide VALUE or --file PATH. Use --file - to read stdin.",
        epilog="Examples:\n  agentself note set handoff --file PATH\n  agentself note set handoff --file -",
    ),
    CommandSpec(
        ("note", "get"),
        "Print a non-secret note",
        f"{_H}.note:get_note",
        configure_named,
        raw=True,
        epilog="Examples:\n  agentself note get handoff\n  agentself note get handoff --raw",
    ),
    CommandSpec(
        ("note", "list"), "List non-secret note names", f"{_H}.note:list_notes"
    ),
    CommandSpec(
        ("note", "delete"),
        "Delete a non-secret note",
        f"{_H}.note:delete_note",
        configure_named,
    ),
    CommandSpec(
        ("note", "exists"),
        "Check whether a non-secret note exists",
        f"{_H}.note:note_exists",
        configure_named,
    ),
    CommandSpec(
        ("email",),
        "Optional email. connect does not block init",
        None,
        args=("connect", "show", "send", "receive", "list", "find", "mark"),
        next="agentself email connect",
        dest="email_command",
        description=(
            "Optional email. receive prints headers only. Fetch a body by ref with "
            "receive REF --file PATH or receive REF --raw."
        ),
        epilog="Examples:\n  agentself email connect\n  agentself email receive REF --raw\n  agentself backends email",
    ),
    CommandSpec(
        ("email", "connect"),
        "Connect email. Does not block init",
        f"{_H}.email:connect_email",
        configure_email_connect,
        description=(
            "Never prompts: returns a setup object and exit 3 when input or a human "
            "action is required. Continue with --continue --state STATE --result-file PATH. "
            "Use --result-file - to read stdin."
        ),
        epilog="Examples:\n  agentself email connect\n  agentself email connect --continue --state STATE --result-file -",
    ),
    CommandSpec(
        ("email", "show"),
        "Print the live email address",
        f"{_H}.email:show_email",
    ),
    CommandSpec(
        ("email", "send"),
        "Send a message. Needs send credentials",
        f"{_H}.email:send_email",
        configure_email_send,
        description="Fails closed without send credentials. See agentself backends email.",
        epilog="Examples:\n  agentself email send TO SUBJECT BODY\n  agentself backends email",
    ),
    CommandSpec(
        ("email", "receive"),
        "Receive new mail, or fetch one ref or provider id",
        f"{_H}.email:receive_email",
        configure_email_receive,
        raw=True,
        description=(
            "Headers are safe by default. Bodies need --file or --raw. "
            "--raw requires a ref or id and writes exact body bytes."
        ),
        epilog="Examples:\n  agentself email receive\n  agentself email receive REF --raw",
    ),
    CommandSpec(
        ("email", "list"),
        "List inbound message headers",
        f"{_H}.email:list_email",
        configure_email_list,
    ),
    CommandSpec(
        ("email", "find"),
        "Find inbound message headers",
        f"{_H}.email:find_email",
        configure_email_find,
        description=(
            "Find a non-empty case-insensitive substring in From, To, or Subject. "
            "Uses header-only listing and never fetches or searches message bodies."
        ),
    ),
    CommandSpec(
        ("email", "mark"),
        "Mark a message acted or unacted",
        f"{_H}.email:mark_email",
        configure_email_mark,
        description=(
            "Unknown compact refs and provider IDs that list/receive have not "
            "stored are refused."
        ),
        epilog="Examples:\n  agentself email mark REF acted",
    ),
    CommandSpec(
        ("wallet",),
        "Show, address, balance, authorize (sign), and send",
        None,
        args=("show", "address", "balance", "authorize", "verify", "send"),
        next="agentself wallet address",
        dest="wallet_command",
        description=(
            "Looking for signing? Use wallet authorize --file PATH. "
            "That is distinct from wallet send."
        ),
        epilog="Examples:\n  agentself wallet address --raw\n  agentself wallet authorize --file PATH\n  agentself wallet send TO AMOUNT",
    ),
    CommandSpec(
        ("wallet", "show"),
        "Print who this identity is / the bound destination",
        f"{_H}.wallet:show_wallet",
        raw=True,
    ),
    CommandSpec(
        ("wallet", "address"),
        "Print the destination identifier",
        f"{_H}.wallet:wallet_address",
        raw=True,
        epilog="Examples:\n  agentself wallet address --raw",
    ),
    CommandSpec(
        ("wallet", "balance"),
        "Print the bound balance",
        f"{_H}.wallet:wallet_balance",
    ),
    CommandSpec(
        ("wallet", "authorize"),
        "Authorize an action or message. The backend picks how.",
        f"{_H}.wallet:authorize_wallet",
        configure_wallet_authorize,
        raw=True,
        description=(
            "Prefer --file PATH or --file -. The output is the signature to attach, "
            "for example an HTTP header or body. It is not a send."
        ),
        epilog="Examples:\n  agentself wallet authorize --file PATH\n  agentself wallet authorize --file - --raw",
    ),
    CommandSpec(
        ("wallet", "verify"),
        "Verify an authorization against this identity",
        f"{_H}.wallet:verify_wallet",
        configure_wallet_verify,
        epilog="Examples:\n  agentself wallet verify --file PATH AUTHORIZATION",
    ),
    CommandSpec(
        ("wallet", "send"),
        "Send an amount of an asset",
        f"{_H}.wallet:send_wallet",
        configure_wallet_send,
        epilog="Examples:\n  agentself wallet send TO AMOUNT\n  agentself wallet send TO AMOUNT ASSET",
    ),
    CommandSpec(
        ("backup",),
        "Copy the identity directory to PATH",
        f"{_H}.identity:backup_identity",
        configure_backup,
        args=("PATH",),
        next="agentself backup PATH",
    ),
    CommandSpec(
        ("restore",),
        "Copy PATH onto the identity directory",
        f"{_H}.identity:restore_identity",
        configure_restore,
        args=("PATH",),
        next="agentself restore PATH",
    ),
    CommandSpec(
        ("install",),
        "Install the agent skill or host tools",
        f"{_H}.identity:install_components",
        configure_install,
        args=("--skills", "--tools"),
        next="agentself install --tools",
        epilog="Examples:\n  agentself install --tools\n  agentself install --skills=agents -g",
    ),
)


def featured_metavar() -> str:
    names = [spec.path[0] for spec in COMMANDS if len(spec.path) == 1]
    return "{" + ",".join(names) + "}"


def spec_for(path: Sequence[str]) -> CommandSpec | None:
    wanted = tuple(path)
    for spec in COMMANDS:
        if spec.path == wanted:
            return spec
    return None


def commands_payload() -> dict[str, object]:
    featured = [
        {
            "name": spec.path[0] if len(spec.path) == 1 else " ".join(spec.path),
            "args": list(spec.args or ()),
            "next": spec.next or "",
        }
        for spec in COMMANDS
        if spec.args is not None
        and (len(spec.path) == 1 or spec.path == ("secret", "list"))
    ]
    raw: dict[str, list[str]] = {}
    for spec in COMMANDS:
        if spec.raw and len(spec.path) >= 2:
            raw.setdefault(spec.path[0], []).append(spec.path[-1])
    return {"commands": featured, "raw": raw}

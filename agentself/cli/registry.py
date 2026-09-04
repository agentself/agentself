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
        help="Name this identity on first init (default agent). A second name in this directory is refused",
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
        help="Allow backend changes on an existing identity",
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
    parser.add_argument(
        "--force",
        dest="force",
        action="store_true",
        help="Replace an existing --file",
    )


def configure_secret_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env",
        dest="env_bindings",
        action="append",
        default=[],
        required=True,
        metavar="VAR=NAME",
        help="Set VAR from secret NAME in the child environment. Repeat",
    )
    parser.add_argument(
        "--unsafe",
        action="store_true",
        help="Allow a protected secret",
    )
    parser.add_argument(
        "child",
        nargs=argparse.REMAINDER,
        metavar="COMMAND",
        help="Child process. Prefer -- COMMAND so flags stay with the child",
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
    parser.add_argument(
        "--interval",
        dest="interval",
        type=float,
        default=None,
        metavar="SECONDS",
        help="With --continue, poll until setup is terminal. The first connect still returns immediately",
    )
    parser.add_argument(
        "--timeout",
        dest="timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Max seconds to poll with --interval (default 300)",
    )


def configure_email_send(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("to", metavar="TO", help="Recipient address")
    parser.add_argument("subject", metavar="SUBJECT", help="Subject")
    parser.add_argument(
        "body",
        nargs="?",
        metavar="BODY",
        help="Body. Omit when using --file PATH",
    )
    parser.add_argument(
        "--file",
        dest="from_file",
        default="",
        metavar="PATH",
        help="Read the body from a file. Use - to read stdin",
    )


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
    parser.add_argument(
        "--limit",
        dest="limit",
        type=int,
        default=None,
        metavar="N",
        help="Max headers for a no-ref check (1-100)",
    )
    parser.add_argument(
        "--force",
        dest="force",
        action="store_true",
        help="Replace an existing --file",
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
        help="Keep messages not marked acted or rejected",
    )
    acted_filter.add_argument(
        "--rejected",
        dest="acted_filter",
        action="store_const",
        const="rejected",
        help="Keep messages marked rejected",
    )


def _add_mail_limit(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--limit",
        dest="limit",
        type=int,
        default=None,
        metavar="N",
        help="Max headers to return (1-100)",
    )


def configure_email_list(parser: argparse.ArgumentParser) -> None:
    _add_mail_filters(parser)
    _add_mail_limit(parser)


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
        choices=("acted", "unacted", "rejected"),
        help="Local task state",
    )


def configure_wallet_authorize(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "message",
        nargs="?",
        metavar="MESSAGE",
        help="Legacy: action or message to authorize. Prefer --file PATH",
    )
    parser.add_argument(
        "--file",
        dest="from_file",
        default="",
        metavar="PATH",
        help="Read the exact statement from a file. Trailing newlines are kept. Use - to read stdin",
    )
    parser.add_argument(
        "--out",
        dest="out_file",
        default="",
        metavar="PATH",
        help="Write the exact authorization to PATH. Cannot be -; use --raw for stdout",
    )
    parser.add_argument(
        "--force",
        dest="force",
        action="store_true",
        help="Replace an existing --out file",
    )


def configure_wallet_verify(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "message",
        nargs="?",
        metavar="MESSAGE",
        help="Legacy: message that was authorized. Prefer --file PATH",
    )
    parser.add_argument(
        "authorization",
        nargs="?",
        metavar="AUTHORIZATION",
        help="Legacy: authorization to check. Prefer --authorization-file PATH",
    )
    parser.add_argument(
        "--file",
        dest="from_file",
        default="",
        metavar="PATH",
        help="Read the exact statement from a file. Trailing newlines are kept. Use - to read stdin",
    )
    parser.add_argument(
        "--authorization-file",
        dest="authorization_file",
        default="",
        metavar="PATH",
        help="Read the authorization from a file instead of argv. Use - to read stdin",
    )


def configure_wallet_balance(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "asset",
        nargs="?",
        default="",
        metavar="ASSET",
        help="Asset to report. Omit to use the backend default",
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
    parser.add_argument(
        "--file",
        dest="from_file",
        default="",
        metavar="PATH",
        help="Extra payment details. The bound wallet interprets the file. Use - to read stdin",
    )
    parser.add_argument(
        "--test",
        dest="test_send",
        action="store_true",
        help="Validate the send plan without broadcasting",
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
            "Create the local identity. One identity per directory; another agent "
            "needs --identity-dir PATH. Email is optional and does not block init. "
            "Needs host tools on PATH (agentself install --tools)."
        ),
        epilog="Examples:\n  agentself --identity-dir PATH init\n  agentself init --id NAME\n  agentself init --wallet-key-file -",
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
        args=("create", "get", "run", "update", "list", "delete", "exists"),
        next="agentself secret list",
        dest="secret_command",
        description=(
            "Named secrets. list prints names only. "
            "run sets env vars for one child process and does not print values. "
            "wallet.key cannot be deleted and needs --unsafe to export or replace."
        ),
        epilog="Examples:\n  agentself secret create NAME VALUE\n  agentself secret get NAME --raw\n  agentself secret run --env VAR=NAME -- COMMAND",
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
        ("secret", "run"),
        "Run a command with secrets in its environment",
        f"{_H}.secret:run_secret",
        configure_secret_run,
        description=(
            "Decrypt named secrets into the child environment for one process. "
            "JSON reports exit, names, env var names, and captured output. "
            "Secret values are redacted from that JSON. "
            "wallet.key also requires --unsafe. "
            "secret get / --file / --raw stay available when the agent needs the value."
        ),
        epilog=(
            "Examples:\n"
            "  agentself secret run --env API_KEY=NAME -- COMMAND\n"
            "  agentself secret run --env API_KEY=NAME -- sh -c "
            "'curl -s -H \"Authorization: Bearer $API_KEY\" URL'"
        ),
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
            "Optional email. receive without a ref prints new-message headers "
            "without fetching bodies or changing seen state. Fetch a body by ref "
            "with receive REF --file PATH or receive REF --raw."
        ),
        epilog="Examples:\n  agentself email connect\n  agentself email receive\n  agentself email receive REF --file PATH\n  agentself backends email",
    ),
    CommandSpec(
        ("email", "connect"),
        "Connect email. Does not block init",
        f"{_H}.email:connect_email",
        configure_email_connect,
        description=(
            "Never prompts: returns a setup object and exit 3 when input or a human "
            "action is required. Continue with --continue --state STATE --result-file PATH. "
            "Use --result-file - to read stdin. With --continue, --interval polls until "
            "setup is terminal. The first connect still returns immediately."
        ),
        epilog="Examples:\n  agentself email connect\n  agentself email connect --continue --state STATE --result-file -\n  agentself email connect --continue --state STATE --interval 5",
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
        description=(
            "BODY may be omitted when using --file PATH. Use --file - to read stdin. "
            "Fails closed without send credentials. See agentself backends email."
        ),
        epilog="Examples:\n  agentself email send TO SUBJECT --file PATH\n  agentself email send TO SUBJECT BODY\n  agentself backends email",
    ),
    CommandSpec(
        ("email", "receive"),
        "Receive new mail, or fetch one ref or provider id",
        f"{_H}.email:receive_email",
        configure_email_receive,
        raw=True,
        description=(
            "Without a ref, print new-message headers through the list path. "
            "That check is repeatable and does not fetch bodies or change seen "
            "state. Explicit refs keep the consuming receive. Bodies need "
            "--file or --raw. --raw requires a ref or id and writes exact body bytes."
        ),
        epilog="Examples:\n  agentself email receive\n  agentself email receive REF --file PATH\n  agentself email receive REF --raw",
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
            "stored are refused. rejected is local refusal state, not acted."
        ),
        epilog="Examples:\n  agentself email mark REF acted\n  agentself email mark REF rejected",
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
        epilog="Examples:\n  agentself --identity-dir PATH wallet address --raw\n  agentself wallet authorize --file PATH --out PATH\n  agentself wallet send TO AMOUNT",
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
        configure_wallet_balance,
        description="Omit ASSET to use the backend default. Named assets are backend-interpreted ids.",
        epilog="Examples:\n  agentself wallet balance\n  agentself wallet balance ASSET",
    ),
    CommandSpec(
        ("wallet", "authorize"),
        "Authorize an action or message. The backend picks how.",
        f"{_H}.wallet:authorize_wallet",
        configure_wallet_authorize,
        raw=True,
        description=(
            "Prefer --file PATH --out PATH. Positional MESSAGE and JSON "
            "`authorization` are legacy CLI 2 forms. The file is the signature to "
            "attach, for example an HTTP header or body. It is not a send. "
            "A typed statement (domain, types, message) is authorized as typed data; "
            "other files stay a personal signature. Login text uses this verb. "
            "Statement files keep trailing newlines; message_sha256 is the exact "
            "decoded statement."
        ),
        epilog="Examples:\n  agentself wallet authorize --file PATH --out PATH\n  agentself wallet authorize --file PATH\n  agentself wallet authorize --file - --raw",
    ),
    CommandSpec(
        ("wallet", "verify"),
        "Verify an authorization against this identity",
        f"{_H}.wallet:verify_wallet",
        configure_wallet_verify,
        description=(
            "Prefer --file PATH --authorization-file PATH. Positional AUTHORIZATION "
            "is a legacy CLI 2 form."
        ),
        epilog="Examples:\n  agentself wallet verify --file PATH --authorization-file PATH\n  agentself wallet verify --file PATH AUTHORIZATION",
    ),
    CommandSpec(
        ("wallet", "send"),
        "Send an amount of an asset",
        f"{_H}.wallet:send_wallet",
        configure_wallet_send,
        description=(
            "Live backends move real funds. --test returns the send plan without "
            "broadcasting. --file is extra payment details interpreted by the bound "
            'wallet. A JSON object {"allow": true} grants TO pull permission when '
            "the wallet supports it."
        ),
        epilog="Examples:\n  agentself wallet send TO AMOUNT\n  agentself wallet send TO AMOUNT --file PATH\n  agentself wallet send TO AMOUNT --test",
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
        description=(
            "install --skills copies the skill into the current workspace. "
            "-g copies it into the user skill directory. Neither uses the "
            "identity directory."
        ),
        epilog="Examples:\n  agentself install --tools\n  agentself install --skills\n  agentself install --skills=agents -g",
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


def command_verbs() -> dict[str, tuple[str, ...]]:
    groups = {spec.path[0] for spec in COMMANDS if spec.handler is None}
    return {
        group: tuple(
            spec.path[1]
            for spec in COMMANDS
            if len(spec.path) == 2 and spec.path[0] == group
        )
        for group in groups
    }


def command_recovery(argv: Sequence[str]) -> tuple[str, str] | None:
    tokens: list[str] = []
    skip_value = False
    for token in argv:
        if token == "--":
            break
        if token in ("--json", "--raw"):
            continue
        if token == "--identity-dir":
            skip_value = True
            continue
        if token.startswith("--identity-dir="):
            continue
        if skip_value:
            skip_value = False
            continue
        tokens.append(token)
    if not tokens or tokens[0].startswith("-"):
        return None
    path = tuple(tokens[:2])
    exact = {
        ("doctor",): ("doctor is now diagnose", "agentself diagnose"),
        ("whoami",): ("whoami is now show", "agentself show"),
        ("start",): ("start is now init", "agentself init"),
        ("remove",): ("remove is not a command", "agentself commands"),
        ("print",): ("print is not a command", "agentself commands"),
        ("set",): ("set needs a resource", "agentself commands"),
        ("secret", "read"): (
            "secret read is now secret get",
            "agentself secret get --help",
        ),
        ("secret", "set"): (
            "secret set is ambiguous; use create or update",
            "agentself secret --help",
        ),
        ("email", "remove"): (
            "email remove is not a command",
            "agentself email --help",
        ),
        ("wallet", "sign"): (
            "wallet sign is now wallet authorize",
            "agentself wallet authorize --help",
        ),
        ("note", "create"): (
            "note create is now note set",
            "agentself note set --help",
        ),
        ("note", "update"): (
            "note update is now note set",
            "agentself note set --help",
        ),
    }
    recovery = exact.get(path) or exact.get(path[:1])
    if recovery is not None:
        return recovery

    aliases = {
        "mail": "email",
        "notes": "note",
        "secrets": "secret",
        "stores": "secret",
    }
    group = aliases.get(tokens[0])
    default_next = f"agentself {group} --help" if group is not None else ""
    if group is None:
        # store is the secret backend, not a verb. Do not hint restore.
        for channel in CHANNELS.values():
            if tokens[0] == channel.name and channel.command_group != channel.name:
                group = channel.command_group
                default_next = channel.next
                break
            if tokens[0] in channel.names:
                group = channel.command_group
                default_next = channel.next
                break
    if group is None:
        return None

    verbs = command_verbs()[group]
    verb = tokens[1] if len(tokens) > 1 and tokens[1] in verbs else ""
    spec = spec_for((group, verb)) if verb else None
    if spec is not None and spec.next:
        default_next = spec.next
    elif verb in {"address", "balance", "list", "show"}:
        default_next = f"agentself {group} {verb}"
    return f"{tokens[0]} maps to the {group} command group", default_next


_SCHEMA_SKIP_DESTS = frozenset({"help", "as_json", "as_raw", "identity_dir", "command"})
_SENSITIVE_DESTS = frozenset(
    {
        "result_file",
        "wallet_key_file",
        "authorization_file",
        "to_file",
        "out_file",
        "body_file",
    }
)


def _param_type(action: argparse.Action) -> str:
    if action.type is int or action.type is float:
        return "number"
    if isinstance(action.choices, (list, tuple)) and action.choices:
        return "choice"
    const = getattr(action, "const", None)
    if action.nargs == 0 or isinstance(const, bool) or action.default is False:
        if not action.option_strings:
            return "string"
        return "bool"
    return "string"


def _param_name(action: argparse.Action) -> str:
    if action.option_strings:
        return action.option_strings[0]
    raw = str(action.metavar or action.dest or "").strip()
    return raw.upper() if raw.islower() else raw


def _param_of(action: argparse.Action) -> dict[str, object] | None:
    if action.dest in _SCHEMA_SKIP_DESTS:
        return None
    name = _param_name(action)
    if not name:
        return None
    required = not action.option_strings and action.nargs not in ("?", "*", 0)
    if action.option_strings and getattr(action, "required", False):
        required = True
    payload: dict[str, object] = {
        "name": name,
        "type": _param_type(action),
        "required": bool(required),
    }
    if action.dest in _SENSITIVE_DESTS:
        payload["sensitive"] = True
    if isinstance(action.choices, (list, tuple)) and action.choices:
        payload["choices"] = [str(item) for item in action.choices]
    return payload


def command_params(spec: CommandSpec) -> list[dict[str, object]]:
    if spec.configure is None:
        return []
    parser = argparse.ArgumentParser(add_help=False)
    spec.configure(parser)
    params: list[dict[str, object]] = []
    for action in parser._actions:
        item = _param_of(action)
        if item is not None:
            params.append(item)
    return params


def _verb_schema(spec: CommandSpec) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": spec.path[-1],
        "raw": spec.raw,
        "params": command_params(spec),
    }
    if spec.next:
        payload["next"] = spec.next
    return payload


def commands_payload(*, email_next: str | None = None) -> dict[str, object]:
    verbs = command_verbs()
    featured: list[dict[str, object]] = []
    for spec in COMMANDS:
        if len(spec.path) != 1 or spec.args is None:
            continue
        nxt = email_next if spec.path == ("email",) and email_next else spec.next or ""
        item: dict[str, object] = {
            "name": spec.path[0],
            "args": list(verbs.get(spec.path[0], spec.args or ())),
            "next": nxt,
        }
        if spec.handler is None:
            item["verbs"] = [
                _verb_schema(child)
                for child in COMMANDS
                if len(child.path) == 2 and child.path[0] == spec.path[0]
            ]
        else:
            params = command_params(spec)
            if params:
                item["params"] = params
        featured.append(item)
    raw: dict[str, list[str]] = {}
    for spec in COMMANDS:
        if spec.raw and len(spec.path) >= 2:
            raw.setdefault(spec.path[0], []).append(spec.path[-1])
    return {"commands": featured, "raw": raw}

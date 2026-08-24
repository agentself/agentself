from __future__ import annotations

import argparse
import json
import sys

from agentself.host import CHANNELS, ENV_IDENTITY_DIR, close_match
from agentself.local import redact_secrets

_HELP = argparse.RawDescriptionHelpFormatter
_FEATURED = "{init,show,backends,commands,diagnose,secret,note,email,wallet,backup,restore,install}"
_COMMANDS = (
    {
        "name": "init",
        "args": [
            "--id",
            "--wallet",
            "--email",
            "--store",
            "--force",
            "--wallet-key-file",
        ],
        "next": "agentself --json init",
    },
    {
        "name": "show",
        "args": [],
        "next": "agentself --json show",
    },
    {
        "name": "backends",
        "args": ["CHANNEL", "BACKEND"],
        "next": "agentself --json backends",
    },
    {
        "name": "commands",
        "args": [],
        "next": "agentself --json commands",
    },
    {
        "name": "diagnose",
        "args": [],
        "next": "agentself --json diagnose",
    },
    {
        "name": "secret",
        "args": ["create", "get", "update", "list", "delete", "exists"],
        "next": "agentself --json secret list",
    },
    {
        "name": "note",
        "args": ["set", "get", "list", "delete", "exists"],
        "next": "agentself --json note list",
    },
    {
        "name": "email",
        "args": ["connect", "show", "send", "receive", "list", "find", "mark"],
        "next": "agentself --json email connect",
    },
    {
        "name": "wallet",
        "args": ["show", "address", "balance", "authorize", "verify", "send"],
        "next": "agentself --json wallet address",
    },
    {
        "name": "backup",
        "args": ["PATH"],
        "next": "agentself backup PATH",
    },
    {
        "name": "restore",
        "args": ["PATH"],
        "next": "agentself restore PATH",
    },
    {
        "name": "install",
        "args": ["--skills", "--tools"],
        "next": "agentself install --tools",
    },
)


def commands_payload() -> dict[str, object]:
    return {"ok": True, "commands": [dict(item) for item in _COMMANDS]}


def format_commands() -> str:
    width = max(len(str(item["name"])) for item in _COMMANDS)
    lines = []
    for item in _COMMANDS:
        args = " ".join(str(part) for part in item["args"])
        name = str(item["name"]).ljust(width)
        lines.append(f"{name}  {args}".rstrip())
    return "\n".join(lines) + "\n"


class _Parser(argparse.ArgumentParser):
    _as_json = False

    def exit(self, status: int = 0, message: str | None = None) -> None:  # type: ignore[override]
        if message:
            stream = sys.stdout if _Parser._as_json else sys.stderr
            self._print_message(message, stream)
        raise SystemExit(status)

    def error(self, message: str) -> None:  # type: ignore[override]
        nxt = f"{self.prog} --help"
        message = redact_secrets(message)
        if _Parser._as_json:
            self.exit(
                2,
                json.dumps(
                    {"ok": False, "error": "refused", "reason": message, "next": nxt}
                )
                + "\n",
            )
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\nnext: {nxt}\n")

    def _check_value(self, action, value):
        if action.choices is None or value in action.choices:
            return
        listing = getattr(action, "_choices_actions", None)
        shown = [item.dest for item in listing] if listing else list(action.choices)
        msg = f"invalid choice: {value!r} (choose from {', '.join(map(repr, shown))})"
        hint = close_match(str(value), shown)
        if hint:
            msg += f" (did you mean {hint!r}?)"
        raise argparse.ArgumentError(action, msg)


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        default=False,
        help="Print one JSON object",
    )


def _cmd(
    sub,
    name: str,
    json_parent: argparse.ArgumentParser,
    *,
    help: str,
    description: str | None = None,
    epilog: str | None = None,
    **kwargs,
) -> argparse.ArgumentParser:
    if description is None and help is not argparse.SUPPRESS:
        description = help
    return sub.add_parser(
        name,
        parents=[json_parent],
        formatter_class=_HELP,
        help=help,
        description=description,
        epilog=epilog,
        **kwargs,
    )


def _add_create_flags(parser: argparse.ArgumentParser) -> None:
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
        help="Secret value. Omit to read stdin when stdin is not a tty",
    )
    parser.add_argument(
        "--file",
        dest="from_file",
        default="",
        metavar="PATH",
        help="Read the value from a file. A leading UTF-8 BOM is dropped",
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
        help="Read exact UTF-8 text and newlines from a file; drops a leading BOM",
    )


def _parser() -> argparse.ArgumentParser:
    json_parent = argparse.ArgumentParser(add_help=False)
    _add_json_flag(json_parent)
    parser = _Parser(
        prog="agentself",
        usage="%(prog)s [--json] [--version] [COMMAND ...]",
        formatter_class=_HELP,
        description=(
            "Local identity for an agent: wallet, secrets, non-secret notes, "
            "and optional email.\n"
            "\n"
            "No command prints the current identity.\n"
            "Use agentself --json commands for the compact verb index.\n"
            "Use agentself backends CHANNEL BACKEND to drill into setup options.\n"
            "Use agentself --json diagnose to check this host.\n"
            "Use --version to print the package version.\n"
            "Prefer --json for one JSON object.\n"
            "Exit codes: 0 ok, 1 error, 2 refused, 3 missing."
        ),
        epilog=(
            "Examples:\n"
            "  agentself --json --version\n"
            "  agentself --json commands\n"
            "  agentself --json diagnose\n"
            "  agentself install --tools\n"
            "  agentself init\n"
            "  agentself backends\n"
            "  agentself --json show\n"
            "  agentself secret create NAME VALUE\n"
            "  agentself note set NAME VALUE\n"
            "  agentself wallet address\n"
            "  agentself install --skills\n"
            "\n"
            f"Identity directory is {ENV_IDENTITY_DIR} (default ~/.agentself)."
        ),
    )
    _add_json_flag(parser)
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the package version",
    )
    sub = parser.add_subparsers(
        dest="command",
        required=False,
        metavar=_FEATURED,
        parser_class=_Parser,
        prog="agentself",
    )

    init_p = _cmd(
        sub,
        "init",
        json_parent,
        help="Create the local identity",
        description=(
            "Create the local identity and print the bound destination "
            "and the public recipient. Email is optional and does not block init.\n"
            "Needs host tools on PATH (agentself install --tools).\n"
            "Backend names: agentself backends"
        ),
        epilog=(
            "Examples:\n"
            "  agentself init\n"
            "  agentself init --id NAME\n"
            "  agentself init --wallet base\n"
            "  agentself init --wallet-key-file PATH\n"
            "  agentself init --force\n"
            "  agentself --json init"
        ),
    )
    _add_create_flags(init_p)
    init_p.add_argument(
        "--force",
        action="store_true",
        help="Allow identity or backend changes on an existing identity",
    )
    init_p.add_argument(
        "--wallet-key-file",
        dest="wallet_key_file",
        default="",
        metavar="PATH",
        help="Seal this hex key as wallet.key on first init. Use - to read stdin",
    )
    init_p.add_argument(
        "--unsafe",
        action="store_true",
        help="Allow replacing wallet.key on an existing identity",
    )

    _cmd(
        sub,
        "show",
        json_parent,
        help="Print the current identity",
        description=(
            "Print the current identity, identity directory, and current backends. "
            "Same as running agentself with no command.\n"
            "If not initialized, exits 2 and prints: next: agentself init"
        ),
        epilog="Examples:\n  agentself show\n  agentself --json show",
    )

    backends_p = _cmd(
        sub,
        "backends",
        json_parent,
        help="List shipped backends",
        description=(
            "List shipped host backends. Default output is names and short summaries. "
            "Same command tree on every backend. "
            "Pick with init flags or AGENTSELF_*_BACKEND. "
            "Flag, then env, then identity-directory config, then default. No failover. "
            "One identity per directory; isolate with AGENTSELF_IDENTITY_DIR, "
            "not named remotes. Drill in with CHANNEL or CHANNEL BACKEND for options."
        ),
        epilog=(
            "Examples:\n"
            "  agentself backends\n"
            "  agentself backends wallet\n"
            "  agentself --json backends\n"
            "  agentself --json backends email\n"
            "  agentself --json backends email agentmail\n"
            "  agentself init --wallet base"
        ),
    )
    names = tuple(CHANNELS)
    backends_p.add_argument(
        "channel",
        nargs="?",
        metavar="{" + ",".join(names) + "}",
        choices=names,
        help="One channel. Omit to list all",
    )
    backends_p.add_argument(
        "backend",
        nargs="?",
        metavar="BACKEND",
        default="",
        help="One backend. Omit to list the channel without option essays",
    )

    _cmd(
        sub,
        "commands",
        json_parent,
        help="List featured verbs",
        description=(
            "List featured verbs with compact args and a next step. "
            "Prefer this over dumping human --help."
        ),
        epilog="Examples:\n  agentself commands\n  agentself --json commands",
    )

    _cmd(
        sub,
        "diagnose",
        json_parent,
        help="Check that this host can run",
        description=(
            "Check that this host can run: Python, age-keygen, the store binary "
            "(the recorded store), the identity directory, and current backends if "
            "initialized. Does not fetch host tools. Missing age/sops: "
            "next: agentself install --tools. "
            "Email without a token or address is not ready, not a failure. "
            "Does not send mail or assets. Prefer --json."
        ),
        epilog="Examples:\n  agentself diagnose\n  agentself --json diagnose",
    )

    secret = _cmd(
        sub,
        "secret",
        json_parent,
        help="Named secrets",
        description=(
            "Named secrets. create refuses if the name exists with a different value. "
            "The same value is unchanged. "
            "update requires it. delete removes a name. list prints names only. "
            "wallet.key cannot be deleted and needs --unsafe to export or replace."
        ),
        epilog=(
            "Examples:\n"
            "  agentself secret create NAME VALUE\n"
            "  agentself secret create NAME --file PATH\n"
            "  agentself secret create --from-dir DIR\n"
            "  agentself secret get NAME\n"
            "  agentself secret exists NAME\n"
            "  agentself secret delete NAME\n"
            "  agentself --json secret list\n"
            "\n"
            "Use agentself secret <command> --help to drill in."
        ),
    )
    secret_sub = secret.add_subparsers(
        dest="secret_command",
        required=True,
        metavar="{create,get,update,list,delete,exists}",
        parser_class=_Parser,
        prog="agentself secret",
    )
    create_p = _cmd(
        secret_sub,
        "create",
        json_parent,
        help="Write a named secret. Refuses if the name exists",
        description=(
            "Write a named secret. Refuses if the name exists with a different value. "
            "Repeating the same value is unchanged. "
            "VALUE may be omitted: reads stdin when stdin is not a tty. "
            "--file PATH drops a leading UTF-8 BOM and keeps a trailing newline. "
            "--from-dir DIR imports each regular file's basename as a name. "
            "--from-files NAME=PATH may be repeated. "
            "wallet.key needs --unsafe."
        ),
        epilog=(
            "Examples:\n"
            "  agentself secret create NAME VALUE\n"
            "  agentself secret create NAME --file PATH\n"
            "  agentself secret create --from-dir DIR\n"
            "  agentself secret create --from-files NAME=PATH\n"
            "  agentself --json secret create NAME VALUE"
        ),
    )
    _add_secret_write_args(create_p, name_required=False)
    create_p.add_argument(
        "--from-dir",
        dest="from_dir",
        default="",
        metavar="DIR",
        help="Import each regular file in DIR; basename is the secret name",
    )
    create_p.add_argument(
        "--from-files",
        dest="from_files",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Import NAME from PATH. Repeat. Same file decode as --file",
    )
    create_p.add_argument(
        "--unsafe",
        action="store_true",
        help="Allow creating wallet.key",
    )
    get_secret = _cmd(
        secret_sub,
        "get",
        json_parent,
        help="Export a named secret safely",
        description=(
            "Export a named secret. Exits 3 if the name is missing. "
            "Use --file for private file output or --meta for size and SHA-256. "
            "Plaintext stdout requires --print; human --print ends with one LF; "
            "--file keeps stored bytes. "
            "wallet.key also requires --unsafe."
        ),
        epilog=(
            "Examples:\n"
            "  agentself secret get NAME --file PATH\n"
            "  agentself secret get NAME --meta\n"
            "  agentself secret get NAME --print"
        ),
    )
    get_secret.add_argument("name", metavar="NAME", help="Secret name")
    secret_output = get_secret.add_mutually_exclusive_group()
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
    secret_output.add_argument(
        "--print",
        dest="print_value",
        action="store_true",
        help="Explicitly print the plaintext value to stdout",
    )
    get_secret.add_argument(
        "--unsafe",
        action="store_true",
        help="Allow a raw export of a protected secret",
    )
    update_p = _cmd(
        secret_sub,
        "update",
        json_parent,
        help="Update a named secret. The name must exist",
        description=(
            "Update a named secret. The name must exist. Same value rules as create. "
            "wallet.key requires --unsafe and must be a hex private key."
        ),
        epilog="Examples:\n  agentself secret update NAME VALUE",
    )
    _add_secret_write_args(update_p)
    update_p.add_argument(
        "--unsafe",
        action="store_true",
        help="Allow replacing a protected secret",
    )
    _cmd(
        secret_sub,
        "list",
        json_parent,
        help="List secret names. Never print values",
        epilog="Examples:\n  agentself secret list\n  agentself --json secret list",
    )
    delete_p = _cmd(
        secret_sub,
        "delete",
        json_parent,
        help="Delete a named secret. wallet.key is protected",
        description=(
            "Delete a named secret. No prompt. wallet.key cannot be deleted. "
            "Mail credentials (email.credential, email.address) can be deleted."
        ),
        epilog="Examples:\n  agentself secret delete NAME\n  agentself --json secret delete NAME",
    )
    delete_p.add_argument("name", metavar="NAME", help="Secret name")
    exists_p = _cmd(
        secret_sub,
        "exists",
        json_parent,
        help="Check that a named secret exists",
        description="Exit 0 if the name exists, 3 if it is missing. Never prints the value.",
        epilog="Examples:\n  agentself secret exists NAME\n  agentself --json secret exists NAME",
    )
    exists_p.add_argument("name", metavar="NAME", help="Secret name")

    note = _cmd(
        sub,
        "note",
        json_parent,
        help="Non-secret identity notes for agent handoff",
        description=(
            "Non-secret identity notes for printable agent handoff context. "
            "Notes are stored in this identity and included by backup/restore. "
            "set is an idempotent upsert: it creates or replaces NAME. "
            "Never store credentials, OTPs, private keys, secret values, or mail bodies."
        ),
        epilog=(
            "Examples:\n"
            "  agentself note set handoff 'next: run tests'\n"
            "  agentself note set handoff --file PATH\n"
            "  agentself note get handoff\n"
            "  agentself --json note list\n"
            "\n"
            "Notes are non-secret and printable. Use agentself secret for secrets."
        ),
    )
    note_sub = note.add_subparsers(
        dest="note_command",
        required=True,
        metavar="{set,get,list,delete,exists}",
        parser_class=_Parser,
        prog="agentself note",
    )
    note_set = _cmd(
        note_sub,
        "set",
        json_parent,
        help="Create or replace a non-secret note",
        description=(
            "Idempotent upsert of a non-secret note. Provide VALUE or --file PATH, "
            "but not both. --file drops a leading UTF-8 BOM and otherwise preserves "
            "UTF-8 text bytes and newlines exactly. Never store credentials, OTPs, "
            "private keys, secret values, or mail bodies."
        ),
        epilog=(
            "Examples:\n"
            "  agentself note set handoff 'next: run tests'\n"
            "  agentself note set handoff --file PATH\n"
            "  agentself --json note set handoff VALUE"
        ),
    )
    _add_note_write_args(note_set)
    note_get = _cmd(
        note_sub,
        "get",
        json_parent,
        help="Print a non-secret note",
        description=(
            "Print a non-secret note value. Notes are printable by default. "
            "Human output ends with one LF. "
            "Never use notes for credentials, OTPs, private keys, secret values, "
            "or mail bodies."
        ),
        epilog="Examples:\n  agentself note get handoff\n  agentself --json note get handoff",
    )
    note_get.add_argument("name", metavar="NAME", help="Note name")
    _cmd(
        note_sub,
        "list",
        json_parent,
        help="List non-secret note names",
        epilog="Examples:\n  agentself note list\n  agentself --json note list",
    )
    note_delete = _cmd(
        note_sub,
        "delete",
        json_parent,
        help="Delete a non-secret note",
        epilog="Examples:\n  agentself note delete handoff",
    )
    note_delete.add_argument("name", metavar="NAME", help="Note name")
    note_exists = _cmd(
        note_sub,
        "exists",
        json_parent,
        help="Check whether a non-secret note exists",
        description="Exit 0 if the note exists, 3 if it is missing.",
        epilog="Examples:\n  agentself note exists handoff",
    )
    note_exists.add_argument("name", metavar="NAME", help="Note name")

    email = _cmd(
        sub,
        "email",
        json_parent,
        help="Optional email. connect does not block init",
        description=(
            "Optional email. connect does not block init. "
            "connect runs a generic, resumable setup. "
            "Backends publish required inputs through agentself backends email. "
            "Send without credentials fails closed. receive prints headers only; "
            "find searches safe headers only. Fetch a body by ref with "
            "receive REF --file PATH."
        ),
        epilog=(
            "Examples:\n"
            "  agentself email connect\n"
            "  agentself email show\n"
            "  agentself --json email receive\n"
            "  agentself email receive REF --file PATH\n"
            "  agentself backends email\n"
            "\n"
            "Use agentself email <command> --help to drill in."
        ),
    )
    email_sub = email.add_subparsers(
        dest="email_command",
        required=True,
        metavar="{connect,show,send,receive,list,find,mark}",
        parser_class=_Parser,
        prog="agentself email",
    )
    connect_p = _cmd(
        email_sub,
        "connect",
        json_parent,
        help="Connect email. Does not block init",
        description=(
            "Connect email. Does not block init. "
            "Interactive use continues setup until connected. "
            "--json never prompts: it returns a generic setup object and exit 3 "
            "when input or a human action is required. "
            "Continue with --json --continue --state STATE --result-file PATH. "
            "Sensitive answers come from --result-file, stdin, or a hidden prompt, "
            "never from argv. Happy path: run connect, obtain the requested backend "
            "credential, provide it through the secure prompt or --result-file, then "
            "verify with agentself --json email show."
        ),
        epilog=(
            "Examples:\n"
            "  agentself email connect\n"
            "  agentself --json email connect --continue --state STATE --result-file PATH\n"
            "  agentself --json email connect\n"
            "  agentself backends email"
        ),
    )
    connect_p.add_argument(
        "--continue",
        dest="do_continue",
        action="store_true",
        help="Continue a pending setup with --state",
    )
    connect_p.add_argument(
        "--state",
        dest="setup_state",
        default="",
        metavar="STATE",
        help="Opaque state from the previous setup response",
    )
    connect_p.add_argument(
        "--result-file",
        dest="result_file",
        default="",
        metavar="PATH",
        help="Read the current setup answer from a file",
    )
    _cmd(
        email_sub,
        "show",
        json_parent,
        help="Print the live email address",
        description=(
            "Print the live email address. Does not invent an address. "
            "Prints 'not configured' when email is optional and not set up."
        ),
        epilog="Examples:\n  agentself email show\n  agentself --json email show",
    )
    email_send = _cmd(
        email_sub,
        "send",
        json_parent,
        help="Send a message. Needs send credentials",
        description=(
            "Send a message. Needs a send-capable email backend and send credentials. "
            "Fails closed otherwise. See agentself backends email."
        ),
        epilog=(
            "Examples:\n"
            "  agentself email send TO SUBJECT BODY\n"
            "  agentself backends email"
        ),
    )
    email_send.add_argument("to", metavar="TO", help="Recipient address")
    email_send.add_argument("subject", metavar="SUBJECT", help="Subject")
    email_send.add_argument("body", metavar="BODY", help="Body")
    email_receive = _cmd(
        email_sub,
        "receive",
        json_parent,
        help="Receive new mail, or fetch one ref or provider id",
        description=(
            "Receive new mail, or fetch one ref or provider id again. "
            "Headers and new/seen status are safe by default; bodies may contain "
            "credentials and are omitted unless --file or --print is explicit. "
            "Stored refs use reserved syntax like m1; unknown refs are refused."
        ),
        epilog=(
            "Examples:\n"
            "  agentself email receive\n"
            "  agentself email receive REF --file PATH\n"
            "  agentself email receive REF --print\n"
            "  agentself --json email receive"
        ),
    )
    email_receive.add_argument(
        "message_id",
        nargs="?",
        metavar="REF_OR_ID",
        help="Fetch this stored short ref or provider id again",
    )
    body_output = email_receive.add_mutually_exclusive_group()
    body_output.add_argument(
        "--file",
        dest="body_file",
        default="",
        metavar="PATH",
        help="Write one message body to a private file; requires a ref or id",
    )
    body_output.add_argument(
        "--print",
        dest="print_body",
        action="store_true",
        help="Explicitly include message bodies on stdout",
    )
    email_list = _cmd(
        email_sub,
        "list",
        json_parent,
        help="List inbound message headers",
        description=(
            "List inbound message headers only. Filter backend read state with "
            "--status, or independent local task state with --acted/--unacted."
        ),
        epilog=(
            "Examples:\n"
            "  agentself email list\n"
            "  agentself email list --status new --unacted\n"
            "  agentself --json email list --acted"
        ),
    )
    _add_mail_filters(email_list)
    email_find = _cmd(
        email_sub,
        "find",
        json_parent,
        help="Find inbound message headers",
        description=(
            "Find a non-empty case-insensitive substring in From, To, or Subject. "
            "Uses header-only listing and never fetches or searches message bodies. "
            "Filter backend read state with --status, or independent local task "
            "state with --acted/--unacted."
        ),
        epilog=(
            "Examples:\n"
            "  agentself email find invoice\n"
            "  agentself email find sender@example.com --status new --unacted\n"
            "  agentself --json email find urgent"
        ),
    )
    email_find.add_argument("query", metavar="QUERY", help="Non-empty header substring")
    _add_mail_filters(email_find)
    email_mark = _cmd(
        email_sub,
        "mark",
        json_parent,
        help="Mark a message acted or unacted",
        description=(
            "Set independent local task state for a stored short ref or provider ID. "
            "This does not change the backend new/seen status."
        ),
        epilog=(
            "Examples:\n"
            "  agentself email mark REF acted\n"
            "  agentself email mark REF unacted\n"
            "  agentself --json email mark REF acted"
        ),
    )
    email_mark.add_argument(
        "message_id",
        metavar="REF_OR_ID",
        help="Stored short ref or provider message ID",
    )
    email_mark.add_argument(
        "mark_state",
        choices=("acted", "unacted"),
        help="Local task state",
    )

    wallet = _cmd(
        sub,
        "wallet",
        json_parent,
        help="Show, address, balance, authorize (sign), and send",
        description=(
            "Show, address, balance, authorize, and send. Looking for signing? "
            "Use wallet authorize --file PATH; JSON output reports the chosen scheme. "
            "That is distinct from wallet send. "
            "address is the destination id. send uses the backend default asset."
        ),
        epilog=(
            "Examples:\n"
            "  agentself wallet address\n"
            "  agentself --json wallet authorize --file PATH\n"
            "  agentself --json wallet balance\n"
            "  agentself wallet send TO AMOUNT\n"
            "\n"
            "Use agentself wallet <command> --help to drill in."
        ),
    )
    wallet_sub = wallet.add_subparsers(
        dest="wallet_command",
        required=True,
        metavar="{show,address,balance,authorize,send,verify}",
        parser_class=_Parser,
        prog="agentself wallet",
    )
    _cmd(
        wallet_sub,
        "show",
        json_parent,
        help="Print who this identity is / the bound destination",
        epilog="Examples:\n  agentself wallet show\n  agentself --json wallet show",
    )
    _cmd(
        wallet_sub,
        "address",
        json_parent,
        help="Print the destination identifier",
        epilog="Examples:\n  agentself wallet address\n  agentself --json wallet address",
    )
    _cmd(
        wallet_sub,
        "balance",
        json_parent,
        help="Print the bound balance",
        epilog="Examples:\n  agentself wallet balance\n  agentself --json wallet balance",
    )
    wallet_auth = _cmd(
        wallet_sub,
        "authorize",
        json_parent,
        help="Authorize an action or message. The backend picks how.",
        description=(
            "Authorize an action or message. The backend picks how. "
            "Prefer --file PATH. A positional message is still accepted. "
            "The output is the signature to attach, for example an HTTP "
            "header or body. It is not a send."
        ),
        epilog=(
            "Examples:\n"
            "  agentself wallet authorize --file PATH\n"
            "  agentself --json wallet authorize --file PATH"
        ),
    )
    wallet_auth.add_argument(
        "message",
        nargs="?",
        metavar="MESSAGE",
        help="Action or message to authorize. Omit to read stdin when stdin is not a tty",
    )
    wallet_auth.add_argument(
        "--file",
        dest="from_file",
        default="",
        metavar="PATH",
        help="Read the message from a file",
    )
    wallet_verify = _cmd(
        wallet_sub,
        "verify",
        json_parent,
        help="Verify an authorization against this identity",
        description=(
            "Verify an authorization against this identity. "
            "The backend picks the scheme. Provider-neutral: no vendor flags."
        ),
        epilog=(
            "Examples:\n"
            "  agentself wallet verify --file PATH AUTHORIZATION\n"
            "  agentself --json wallet verify --file PATH AUTHORIZATION"
        ),
    )
    wallet_verify.add_argument(
        "message",
        nargs="?",
        metavar="MESSAGE",
        help="Message that was authorized. Omit when using --file; then AUTHORIZATION is the first positional",
    )
    wallet_verify.add_argument(
        "authorization",
        nargs="?",
        metavar="AUTHORIZATION",
        help="Authorization to check",
    )
    wallet_verify.add_argument(
        "--file",
        dest="from_file",
        default="",
        metavar="PATH",
        help="Read the message from a file",
    )
    wallet_send = _cmd(
        wallet_sub,
        "send",
        json_parent,
        help="Send an amount of an asset",
        description=(
            "Send an amount of an asset. "
            "ASSET is optional; the backend supplies its default. "
            "Refuses without gas or when the backend cannot send."
        ),
        epilog=(
            "Examples:\n"
            "  agentself wallet send TO AMOUNT\n"
            "  agentself wallet send TO AMOUNT ASSET\n"
            "  agentself --json wallet send TO AMOUNT"
        ),
    )
    wallet_send.add_argument("to", metavar="TO", help="Destination id")
    wallet_send.add_argument("amount", metavar="AMOUNT", help="Amount to send")
    wallet_send.add_argument(
        "asset",
        nargs="?",
        default="",
        metavar="ASSET",
        help="Asset to send. Omit to use the backend default",
    )

    backup_p = _cmd(
        sub,
        "backup",
        json_parent,
        help="Copy the identity directory to PATH",
        description=(
            "Copy the whole identity directory (config, age key, secrets, notes, mail cache). "
            "Refuses if PATH exists and is not empty, unless --force. "
            "The live age key stays plaintext on the host."
        ),
        epilog="Examples:\n  agentself backup PATH\n  agentself --json backup PATH",
    )
    backup_p.add_argument("path", metavar="PATH", help="Destination directory")
    backup_p.add_argument(
        "--force",
        action="store_true",
        help="Replace a non-empty destination",
    )

    restore_p = _cmd(
        sub,
        "restore",
        json_parent,
        help="Copy PATH onto the identity directory",
        description=(
            "Copy PATH onto the current identity directory. "
            "Refuses if the destination exists and is not empty, unless --force. "
            "Does not print the age key or secret values."
        ),
        epilog="Examples:\n  agentself restore PATH\n  agentself restore PATH --force",
    )
    restore_p.add_argument("path", metavar="PATH", help="Source directory")
    restore_p.add_argument(
        "--force",
        action="store_true",
        help="Replace a non-empty identity directory",
    )

    install_p = _cmd(
        sub,
        "install",
        json_parent,
        help="Install the agent skill or host tools",
        description=(
            "Copy the optional agent skill, or fetch pinned host tools. "
            "agentself install with neither --skills nor --tools is an error. "
            "Skills install into the current directory. "
            "-g / --global writes them under the user home directory. "
            "Skills-less operation is fine: --help and --json are enough."
        ),
        epilog=(
            "Examples:\n"
            "  agentself install --tools\n"
            "  agentself install --skills\n"
            "  agentself install --skills -g\n"
            "  agentself install --skills=agents\n"
            "  agentself install --skills=agents -g"
        ),
    )
    install_p.add_argument(
        "--skills",
        nargs="?",
        const="claude",
        default=None,
        metavar="TARGET",
        help="Copy the skill: claude (default), or agents",
    )
    install_p.add_argument(
        "--tools",
        action="store_true",
        help="Fetch pinned host tools into the host tools dir",
    )
    install_p.add_argument(
        "-g",
        "--global",
        dest="global_install",
        action="store_true",
        help="Install skills under the user home directory",
    )
    return parser

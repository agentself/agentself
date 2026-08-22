from __future__ import annotations

import argparse
import json
import sys

from agentself.host import CHANNELS, ENV_VAULT_ROOT, close_match
from agentself.local import redact_secrets

_HELP = argparse.RawDescriptionHelpFormatter
_FEATURED = "{init,show,backends,diagnose,secret,email,wallet,backup,restore,install}"


class _Parser(argparse.ArgumentParser):
    _as_json = False

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
        if not shown:
            shown = list(action.choices)
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


def _json_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    _add_json_flag(parent)
    return parent


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
    desc = description
    if desc is None and help is not argparse.SUPPRESS:
        desc = help
    return sub.add_parser(
        name,
        parents=[json_parent],
        formatter_class=_HELP,
        help=help,
        description=desc,
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
        if spec.env is None:
            parser.add_argument(
                spec.flag,
                dest=spec.name,
                choices=spec.names,
                default=spec.default,
                help=spec.flag_help(),
            )
        else:
            parser.add_argument(
                spec.flag,
                dest=spec.name,
                default="",
                help=spec.flag_help(),
            )


def _add_secret_write_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("name", metavar="NAME", help="Secret name")
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
        help="Read the value from a file",
    )


def _add_email_id_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "message_id",
        nargs="?",
        metavar="ID",
        help="Fetch this id again even if already received",
    )


def _parser() -> argparse.ArgumentParser:
    json_parent = _json_parent()
    parser = _Parser(
        prog="agentself",
        usage="%(prog)s [--json] [--version] [COMMAND ...]",
        formatter_class=_HELP,
        description=(
            "Local identity for an agent: wallet, secrets, and optional email.\n"
            "\n"
            "No command prints the current identity.\n"
            "Use agentself <command> --help to drill in.\n"
            "Use agentself backends to list shipped backends.\n"
            "Use agentself diagnose to check this host.\n"
            "Use --version to print the package version.\n"
            "Prefer --json for one JSON object.\n"
            "Exit codes: 0 ok, 1 error, 2 refused, 3 missing."
        ),
        epilog=(
            "Examples:\n"
            "  agentself --help\n"
            "  agentself --version\n"
            "  agentself install --tools\n"
            "  agentself init\n"
            "  agentself backends\n"
            "  agentself diagnose\n"
            "  agentself --json show\n"
            "  agentself secret create NAME VALUE\n"
            "  agentself wallet address\n"
            "  agentself install --skills\n"
            "\n"
            f"Identity directory is {ENV_VAULT_ROOT} (default ~/.agentself)."
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
            "  agentself --json init"
        ),
    )
    _add_create_flags(init_p)

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
            "List shipped host backends and their host knobs. Same command tree on "
            "every backend; each backend lists live/local and supported verbs. "
            "Pick with init flags or AGENTSELF_*_BACKEND. "
            "Flag, then env, then identity-directory config, then default. No failover. "
            "One identity per directory; isolate with AGENTSELF_VAULT_ROOT, "
            "not named remotes."
        ),
        epilog=(
            "Examples:\n"
            "  agentself backends\n"
            "  agentself backends wallet\n"
            "  agentself --json backends\n"
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
            "Does not send mail or USDC. Prefer --json."
        ),
        epilog="Examples:\n  agentself diagnose\n  agentself --json diagnose",
    )

    secret = _cmd(
        sub,
        "secret",
        json_parent,
        help="Named secrets",
        description=(
            "Named secrets. create refuses if the name exists. "
            "update requires it. delete removes a name. list prints names only. "
            "wallet.key cannot be deleted."
        ),
        epilog=(
            "Examples:\n"
            "  agentself secret create NAME VALUE\n"
            "  agentself secret get NAME\n"
            "  agentself secret delete NAME\n"
            "  agentself --json secret list\n"
            "\n"
            "Use agentself secret <command> --help to drill in."
        ),
    )
    secret_sub = secret.add_subparsers(
        dest="secret_command",
        required=True,
        metavar="{create,get,update,list,delete}",
        parser_class=_Parser,
        prog="agentself secret",
    )
    create_p = _cmd(
        secret_sub,
        "create",
        json_parent,
        help="Write a named secret. Refuses if the name exists",
        description=(
            "Write a named secret. Refuses if the name exists. "
            "VALUE may be omitted: reads stdin when stdin is not a tty, or --file PATH."
        ),
        epilog=(
            "Examples:\n"
            "  agentself secret create NAME VALUE\n"
            "  agentself secret create NAME --file PATH\n"
            "  agentself --json secret create NAME VALUE"
        ),
    )
    _add_secret_write_args(create_p)
    get_secret = _cmd(
        secret_sub,
        "get",
        json_parent,
        help="Print a named secret",
        description="Print a named secret. Exits 3 if the name is missing.",
        epilog="Examples:\n  agentself secret get NAME\n  agentself --json secret get NAME",
    )
    get_secret.add_argument("name", metavar="NAME", help="Secret name")
    update_p = _cmd(
        secret_sub,
        "update",
        json_parent,
        help="Update a named secret. The name must exist",
        description="Update a named secret. The name must exist. Same VALUE rules as create.",
        epilog="Examples:\n  agentself secret update NAME VALUE",
    )
    _add_secret_write_args(update_p)
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
            "Mail credentials (email.send.token, email.address) can be deleted."
        ),
        epilog="Examples:\n  agentself secret delete NAME\n  agentself --json secret delete NAME",
    )
    delete_p.add_argument("name", metavar="NAME", help="Secret name")

    email = _cmd(
        sub,
        "email",
        json_parent,
        help="Optional email. connect does not block init",
        description=(
            "Optional email. connect does not block init. "
            "AgentMail: store email.send.token, then email connect. "
            "IMAP: store email.address and email.send.token. "
            "Send without credentials fails closed."
        ),
        epilog=(
            "Examples:\n"
            "  agentself secret create email.send.token TOKEN\n"
            "  agentself email connect\n"
            "  agentself email show\n"
            "  agentself --json email receive\n"
            "\n"
            "Use agentself email <command> --help to drill in."
        ),
    )
    email_sub = email.add_subparsers(
        dest="email_command",
        required=True,
        metavar="{connect,show,send,receive,list}",
        parser_class=_Parser,
        prog="agentself email",
    )
    _cmd(
        email_sub,
        "connect",
        json_parent,
        help="Connect email. Does not block init",
        description=(
            "Connect email. Does not block init. "
            "AgentMail: persist the live inbox from secret email.send.token. "
            "IMAP: uses stored email.address. Does not invent principal@domain."
        ),
        epilog=(
            "Examples:\n"
            "  agentself secret create email.send.token TOKEN\n"
            "  agentself email connect"
        ),
    )
    _cmd(
        email_sub,
        "show",
        json_parent,
        help="Print the live email address",
        description=(
            "Print the live email address. Does not invent principal@domain. "
            "Prints 'not configured' when no owned address."
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
            "Fails closed otherwise. "
            "agentmail: secret email.send.token, then email connect. "
            "imap: secrets email.send.token then email.address. "
            "See agentself backends email."
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
        help="Receive new mail, or fetch one id again",
        description="Receive new mail, or fetch one id again even if already received.",
        epilog=(
            "Examples:\n"
            "  agentself email receive\n"
            "  agentself email receive ID\n"
            "  agentself --json email receive"
        ),
    )
    _add_email_id_arg(email_receive)
    _cmd(
        email_sub,
        "list",
        json_parent,
        help="List inbound messages",
        epilog="Examples:\n  agentself email list\n  agentself --json email list",
    )

    wallet = _cmd(
        sub,
        "wallet",
        json_parent,
        help="Show, address, balance, authorize, and send",
        description=(
            "Show, address, balance, authorize, and send. "
            "address is the destination id. send defaults to USDC."
        ),
        epilog=(
            "Examples:\n"
            "  agentself wallet address\n"
            "  agentself --json wallet balance\n"
            "  agentself wallet send TO AMOUNT\n"
            "\n"
            "Use agentself wallet <command> --help to drill in."
        ),
    )
    wallet_sub = wallet.add_subparsers(
        dest="wallet_command",
        required=True,
        metavar="{show,address,balance,authorize,send}",
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
        description="Print the destination identifier",
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
        epilog="Examples:\n  agentself wallet authorize MESSAGE",
    )
    wallet_auth.add_argument(
        "message", metavar="MESSAGE", help="Action or message to authorize"
    )
    wallet_send = _cmd(
        wallet_sub,
        "send",
        json_parent,
        help="Send an amount of an asset. Asset defaults to USDC",
        description=(
            "Send an amount of an asset. Asset defaults to USDC. "
            "Refuses without gas or when the backend cannot send."
        ),
        epilog=(
            "Examples:\n"
            "  agentself wallet send TO AMOUNT\n"
            "  agentself wallet send TO AMOUNT USDC\n"
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
        help="Asset to send (default USDC)",
    )

    backup_p = _cmd(
        sub,
        "backup",
        json_parent,
        help="Copy the identity directory to PATH",
        description=(
            "Copy the whole identity directory (config, age key, secrets, mail cache). "
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
            "-g / --global is for skills, not tools. "
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

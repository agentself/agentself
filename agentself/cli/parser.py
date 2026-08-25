from __future__ import annotations

import argparse
import json
import sys

from agentself.cli.registry import COMMANDS, featured_metavar
from agentself.host import ENV_IDENTITY_DIR, close_match
from agentself.local import redact_secrets

_HELP = argparse.RawDescriptionHelpFormatter
_FEATURED = featured_metavar()


class _Parser(argparse.ArgumentParser):
    def exit(self, status: int = 0, message: str | None = None) -> None:  # type: ignore[override]
        if message:
            self._print_message(message, sys.stdout)
        raise SystemExit(status)

    def error(self, message: str) -> None:  # type: ignore[override]
        nxt = f"{self.prog} --help"
        message = redact_secrets(message)
        self.exit(
            2,
            json.dumps(
                {"ok": False, "error": "refused", "reason": message, "next": nxt}
            )
            + "\n",
        )

    def parse_known_args(self, args=None, namespace=None):
        # Subparsers parse via parse_known_args, so extras would otherwise
        # bubble to the root parser and report `agentself --help`.
        namespace, extras = super().parse_known_args(args, namespace)
        if extras:
            self.error(f"unrecognized arguments: {' '.join(extras)}")
        return namespace, extras

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


def add_global_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--raw",
        dest="as_raw",
        action="store_true",
        default=False,
        help="Write exact bytes for allowlisted commands",
    )


def _cmd(
    sub,
    name: str,
    flag_parent: argparse.ArgumentParser,
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
        parents=[flag_parent],
        formatter_class=_HELP,
        help=help,
        description=description,
        epilog=epilog,
        **kwargs,
    )


def _parser() -> argparse.ArgumentParser:
    flags = argparse.ArgumentParser(add_help=False)
    add_global_flags(flags)
    parser = _Parser(
        prog="agentself",
        usage="%(prog)s [--raw] [--version] [COMMAND ...]",
        formatter_class=_HELP,
        description=(
            "Local identity for an agent: wallet, secrets, non-secret notes, "
            "and optional email.\n"
            "Every command prints one JSON object. Use --raw for exact bytes "
            "on allowlisted commands. Use agentself commands for the verb index.\n"
            "Exit codes: 0 ok, 1 error, 2 refused, 3 missing."
        ),
        epilog=(
            "Examples:\n"
            "  agentself --version\n"
            "  agentself commands\n"
            "  agentself init\n"
            "  agentself secret get NAME --raw\n"
            "  agentself wallet address --raw\n"
            f"Identity directory is {ENV_IDENTITY_DIR} (default ~/.agentself)."
        ),
    )
    add_global_flags(parser)
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
    groups: dict[tuple[str, ...], argparse._SubParsersAction] = {(): sub}
    for spec in COMMANDS:
        parent_path = spec.path[:-1]
        name = spec.path[-1]
        parent_sub = groups[parent_path]
        command_parser = _cmd(
            parent_sub,
            name,
            flags,
            help=spec.summary,
            description=spec.description,
            epilog=spec.epilog,
        )
        if spec.configure is not None:
            spec.configure(command_parser)
        children = [item.path[-1] for item in COMMANDS if item.path[:-1] == spec.path]
        if spec.handler is None:
            dest = spec.dest or f"{name}_command"
            groups[spec.path] = command_parser.add_subparsers(
                dest=dest,
                required=True,
                metavar="{" + ",".join(children) + "}",
                parser_class=_Parser,
                prog="agentself " + " ".join(spec.path),
            )
    return parser

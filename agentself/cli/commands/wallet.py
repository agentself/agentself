from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

from agentself.cli.io import load_value_file, store_value_file, value_meta
from agentself.cli.outcomes import CliOutcome, CliRaw, CliSuccess
from agentself.cli.runtime import client, fail, message_from_args, value_source_error
from agentself.internal.text import sha256_text


def show_wallet(args, vault: Path) -> CliOutcome:
    return _address(args, vault)


def wallet_address(args, vault: Path) -> CliOutcome:
    return _address(args, vault)


def _address(args, vault: Path) -> CliOutcome:
    addr = client(vault).wallet_address()
    if getattr(args, "as_raw", False):
        return CliRaw(addr)
    return CliSuccess({"address": addr})


def wallet_balance(args, vault: Path) -> CliOutcome:
    return CliSuccess(dict(client(vault).wallet_balance()))


def authorize_wallet(args, vault: Path) -> CliOutcome:
    message, err = message_from_args(args)
    if err is not None or message is None:
        return value_source_error(
            args,
            err or "need a value",
            "agentself wallet authorize --help",
        )
    out_file = (getattr(args, "out_file", None) or "").strip()
    if out_file == "-":
        return fail(
            args,
            2,
            "refused",
            "--out cannot be -; use --raw",
            nxt="agentself wallet authorize --help",
        )
    access = client(vault)
    token = access.wallet_authorize(message)
    if getattr(args, "as_raw", False):
        return CliRaw(token)
    addr = access.wallet_address()
    view = access.identity().get("wallet")
    wallet = view if isinstance(view, dict) else {}
    checked = access.wallet_verify(message, token)
    payload: dict[str, object] = {
        "address": addr,
        "scheme": str(checked.get("scheme") or wallet.get("scheme") or ""),
        "network": str(wallet.get("chain") or ""),
        "message_sha256": sha256_text(message),
    }
    if out_file:
        try:
            store_value_file(out_file, token)
        except OSError:
            return fail(
                args,
                1,
                "error",
                "file",
                nxt="agentself wallet authorize --help",
            )
        meta = value_meta(token)
        payload["authorization_file"] = out_file
        payload["authorization_bytes"] = meta["bytes"]
        return CliSuccess(payload)
    payload["authorization"] = token
    return CliSuccess(payload)


def verify_wallet(args, vault: Path) -> CliOutcome:
    path = (args.from_file or "").strip()
    authorization = (args.authorization or "").strip()
    leftover = (args.message or "").strip()
    auth_file = (getattr(args, "authorization_file", None) or "").strip()
    if path and leftover and not authorization and not auth_file:
        authorization = leftover
        args.message = ""
    if auth_file and authorization:
        return fail(
            args,
            2,
            "refused",
            "authorization and --authorization-file",
            nxt="agentself wallet verify --help",
        )
    if auth_file:
        try:
            authorization = load_value_file(auth_file, strip_newline=True)
        except (OSError, UnicodeDecodeError):
            return fail(
                args,
                1,
                "error",
                "file",
                nxt="agentself wallet verify --help",
            )
    message, err = message_from_args(args)
    if err is not None or message is None:
        return value_source_error(
            args,
            err or "need a value",
            "agentself wallet verify --help",
        )
    if not authorization:
        return fail(
            args,
            3,
            "missing",
            "need an authorization",
            nxt="agentself wallet verify --help",
        )
    checked = client(vault).wallet_verify(message, authorization)
    scheme = str(checked.get("scheme") or "").strip()
    if not scheme:
        return fail(
            args,
            1,
            "error",
            "missing scheme",
            nxt="agentself backends wallet",
        )
    valid = bool(checked.get("valid"))
    payload = {
        "valid": valid,
        "address": checked.get("address"),
        "scheme": scheme,
    }
    if valid:
        return CliSuccess(payload)
    return fail(
        args,
        2,
        "refused",
        "invalid authorization",
        nxt="agentself wallet verify --help",
        extra=payload,
    )


def send_wallet(args, vault: Path) -> CliOutcome:
    sent = client(vault).wallet_send(args.to, args.amount, args.asset or "")
    payload: dict[str, object] = {
        "to": args.to,
        "amount": _canonical_amount(args.amount),
        "asset": sent["asset"],
    }
    if sent.get("hash"):
        payload["hash"] = sent["hash"]
    return CliSuccess(payload, redact=False)


def _canonical_amount(value: str) -> str:
    try:
        text = format(Decimal(str(value).strip()), "f")
    except (InvalidOperation, ValueError):
        return str(value)
    return text.rstrip("0").rstrip(".") if "." in text else text

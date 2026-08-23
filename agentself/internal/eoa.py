from __future__ import annotations


def hex_0x(raw: str) -> str:
    return raw if raw.startswith("0x") else "0x" + raw


def generate_secp256k1() -> str:
    """Never log this value."""

    from eth_account import Account

    return hex_0x(Account.create().key.hex())

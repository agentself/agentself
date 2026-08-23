from __future__ import annotations

from agentself.internal.text import UTF8_BOM

_HEX = frozenset("0123456789abcdefABCDEF")


def hex_0x(raw: str) -> str:
    return raw if raw.startswith("0x") else "0x" + raw


def parse_secp256k1_hex(value: str) -> str | None:
    """Return 0x + 64 hex digits, or None. A leading BOM and surrounding whitespace are ignored."""

    text = value.removeprefix(UTF8_BOM).strip()
    if text.startswith(("0x", "0X")):
        text = text[2:]
    if len(text) != 64 or any(ch not in _HEX for ch in text):
        return None
    return hex_0x(text)


def generate_secp256k1() -> str:
    """Never log this value."""

    from eth_account import Account

    return hex_0x(Account.create().key.hex())

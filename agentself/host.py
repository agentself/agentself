from __future__ import annotations

import difflib
from collections.abc import Iterable
from dataclasses import dataclass

from agentself.email_catalog import AGENTMAIL_OPTIONS, IMAP_OPTIONS
from agentself.internal.setup import public_setup_option, setup_option
from agentself.internal.store_tools import store_required_tools

ENV_PREFIX = "AGENTSELF_"
ENV_IDENTITY_DIR = "AGENTSELF_IDENTITY_DIR"
ENV_MAIL_DOMAIN = "AGENTSELF_MAIL_DOMAIN"
ENV_MAIL_HOST = "AGENTSELF_MAIL_HOST"
ENV_IMAP_HOST = "AGENTSELF_IMAP_HOST"
ENV_SMTP_HOST = "AGENTSELF_SMTP_HOST"
ENV_IMAP_PORT = "AGENTSELF_IMAP_PORT"
ENV_SMTP_PORT = "AGENTSELF_SMTP_PORT"
ENV_MAIL_USER = "AGENTSELF_MAIL_USER"
ENV_ETH_RPC_URL = "AGENTSELF_ETH_RPC_URL"
ENV_WALLET_BACKEND = "AGENTSELF_WALLET_BACKEND"
ENV_EMAIL_BACKEND = "AGENTSELF_EMAIL_BACKEND"
ENV_IDENTITY_ID = "AGENTSELF_IDENTITY_ID"
ENV_LOG = "AGENTSELF_LOG"
ENV_FETCH_TOOLS = "AGENTSELF_FETCH_TOOLS"
ENV_TOOLS_DIR = "AGENTSELF_TOOLS"

# age's own variable. Not product-prefixed.
ENV_AGE_KEY_FILE = "AGE_KEY_FILE"

_WALLET_LIVE_VERBS = ("show", "address", "balance", "authorize", "send", "verify")
_MAIL_VERBS = ("connect", "show", "send", "receive", "list")
_STORE_VERBS = ("create", "get", "update", "list", "delete")


class UnknownBind(ValueError):
    def __init__(self, channel: str, value: str = "") -> None:
        self.channel = channel
        self.value = value
        super().__init__(unknown_bind_message(channel, value))


@dataclass(frozen=True)
class Bind:
    name: str
    summary: str
    live: bool = False
    verbs: tuple[str, ...] = ()
    custody: str = ""
    network: str = ""
    asset: str = ""
    options: tuple[dict[str, object], ...] = ()
    tools: tuple[str, ...] = ()
    installable_tools: tuple[str, ...] = ()

    def as_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "summary": self.summary,
            "live": self.live,
            "verbs": list(self.verbs),
            "custody": self.custody,
            "network": self.network,
            "asset": self.asset,
            "options": [public_setup_option(item) for item in self.options],
        }


@dataclass(frozen=True)
class Channel:
    name: str
    env: str | None
    config_key: str | None
    default: str
    binds: tuple[Bind, ...]
    note: str = ""

    @property
    def flag(self) -> str:
        return f"--{self.name}"

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.binds)

    def flag_help(self) -> str:
        parts = [_bind_label(item.name, self.default) for item in self.binds]
        return f"{self.name.capitalize()} backend: {', '.join(parts)}"


CHANNELS: dict[str, Channel] = {
    "wallet": Channel(
        name="wallet",
        env=ENV_WALLET_BACKEND,
        config_key="wallet_backend",
        default="base",
        binds=(
            Bind(
                "base",
                "USDC destination on Base",
                live=True,
                verbs=_WALLET_LIVE_VERBS,
                custody="eoa-key",
                network="base",
                asset="USDC",
            ),
            Bind(
                "ethereum",
                "USDC destination on Ethereum",
                live=True,
                verbs=_WALLET_LIVE_VERBS,
                custody="eoa-key",
                network="ethereum",
                asset="USDC",
                options=(
                    setup_option(
                        name="rpc_url",
                        type="string",
                        source=ENV_ETH_RPC_URL,
                        help="JSON-RPC URL override",
                    ),
                ),
            ),
        ),
        note=f"RPC override: {ENV_ETH_RPC_URL}",
    ),
    "email": Channel(
        name="email",
        env=ENV_EMAIL_BACKEND,
        config_key="email_backend",
        default="agentmail",
        binds=(
            Bind(
                "agentmail",
                "HTTP inbox. Connect provisions or selects an owned address",
                live=True,
                verbs=_MAIL_VERBS,
                custody="none",
                network="agentmail",
                options=AGENTMAIL_OPTIONS,
            ),
            Bind(
                "imap",
                "IMAP receive plus SMTP send. Hosts default to imap./smtp. of the address domain",
                live=True,
                verbs=_MAIL_VERBS,
                custody="none",
                network="imap-smtp",
                options=IMAP_OPTIONS,
            ),
        ),
        note="email connect does not block init.",
    ),
    "store": Channel(
        name="store",
        env=None,
        config_key=None,
        default="sops",
        binds=(
            Bind(
                "sops",
                "age files on this computer",
                live=False,
                verbs=_STORE_VERBS,
                custody="age-files",
                tools=tuple(tool.name for tool in store_required_tools("sops")),
                installable_tools=tuple(
                    tool.name
                    for tool in store_required_tools("sops")
                    if tool.installable
                ),
            ),
            Bind(
                "pass",
                "gpg password store",
                live=False,
                verbs=_STORE_VERBS,
                custody="gpg-pass",
                tools=tuple(tool.name for tool in store_required_tools("pass")),
            ),
        ),
        note="Recorded on the identity at init. Not an env override.",
    ),
}


def bind_of(channel: str, name: str) -> Bind | None:
    spec = CHANNELS.get(channel)
    if spec is None:
        return None
    return next((item for item in spec.binds if item.name == name), None)


def close_match(value: str, names: Iterable[str]) -> str | None:
    if not value:
        return None
    matches = difflib.get_close_matches(value, list(names), n=1, cutoff=0.6)
    return matches[0] if matches else None


def unknown_bind_message(channel: str, value: str = "") -> str:
    spec = CHANNELS.get(channel)
    if spec is None:
        hint = close_match(channel, CHANNELS)
        if hint:
            return f"unknown channel (did you mean {hint}?)"
        return "unknown channel"
    if not value:
        return f"unknown {channel} backend"
    hint = close_match(value, spec.names)
    msg = f"unknown {channel} backend: {value}"
    if hint:
        msg += f" (did you mean {hint}?)"
    return msg


def unknown_bind(channel: str, value: str) -> str | None:
    spec = CHANNELS.get(channel)
    if spec is None or value not in spec.names:
        return unknown_bind_message(channel, value)
    return None


def backends_payload(channel: str | None = None) -> dict[str, object]:
    if channel:
        spec = CHANNELS[channel]
        return {"ok": True, "channel": _channel_json(spec)}
    return {
        "ok": True,
        "prefix": ENV_PREFIX,
        "identity_dir": ENV_IDENTITY_DIR,
        "order": "flag, then env, then identity-directory config, then default",
        "failover": False,
        "channels": [_channel_json(spec) for spec in CHANNELS.values()],
    }


def format_backends(channel: str | None = None) -> str:
    if channel:
        return _format_channel(CHANNELS[channel]) + "\n"
    lines = [
        "Shipped backends. Same command tree on every backend.",
        "Each backend lists live/local, custody, and supported verbs.",
        "Pick with init flags or AGENTSELF_*_BACKEND.",
        "Flag, then env, then identity-directory config, then default. No failover.",
        f"Identity directory: {ENV_IDENTITY_DIR} (default ~/.agentself).",
        f"One identity per directory. Isolate with {ENV_IDENTITY_DIR}, not named remotes.",
        "",
    ]
    for spec in CHANNELS.values():
        lines.append(_format_channel(spec))
        lines.append("")
    lines.append("Drill in: agentself backends CHANNEL")
    lines.append("Current mounts: agentself show")
    return "\n".join(lines).rstrip() + "\n"


def _channel_json(spec: Channel) -> dict[str, object]:
    return {
        "name": spec.name,
        "env": spec.env,
        "flag": spec.flag,
        "config": spec.config_key,
        "default": spec.default,
        "note": spec.note,
        "backends": [item.as_json() for item in spec.binds],
    }


def _format_options(item: Bind) -> str:
    parts: list[str] = []
    for opt in item.options:
        name = str(opt.get("name") or "")
        source = str(opt.get("source") or "")
        if not name:
            continue
        parts.append(f"{name} ({source})" if source else name)
    return "  ".join(parts)


def _format_caps(item: Bind) -> str:
    parts = [f"live:{str(item.live).lower()}"]
    if item.custody:
        parts.append(f"custody:{item.custody}")
    if item.network:
        parts.append(f"network:{item.network}")
    if item.asset:
        parts.append(f"asset:{item.asset}")
    if item.verbs:
        parts.append("verbs:" + ",".join(item.verbs))
    return "  ".join(parts)


def _bind_label(name: str, default: str) -> str:
    return f"{name} (default)" if name == default else name


def _format_channel(spec: Channel) -> str:
    meta = [spec.env, spec.flag] if spec.env else [spec.flag]
    lines = [f"{spec.name}  ({', '.join(meta)})"]
    labels = [_bind_label(item.name, spec.default) for item in spec.binds]
    width = max(len(label) for label in labels)
    for item, label in zip(spec.binds, labels):
        lines.append(f"  {label.ljust(width)}  {item.summary}")
        if item.options:
            lines.append(f"  {' ' * width}  {_format_options(item)}")
        lines.append(f"  {' ' * width}  {_format_caps(item)}")
    if spec.note:
        lines.append(f"  {spec.note}")
    return "\n".join(lines)

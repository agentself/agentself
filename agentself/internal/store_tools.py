"""Provider-neutral host-tool requirements for store bindings."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HostTool:
    name: str
    installable: bool = False


_STORE_TOOLS: dict[str, tuple[HostTool, ...]] = {
    "sops": (HostTool("sops", installable=True),),
    "pass": (HostTool("gpg"), HostTool("pass")),
}


def store_required_tools(binding: str) -> tuple[HostTool, ...]:
    """Return the host tools required by a store binding."""

    return _STORE_TOOLS.get(binding, ())

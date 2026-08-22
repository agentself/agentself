"""GNUPGHOME helpers. Keys stay in the vault; sockets must fit unix sockaddr."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

# Longest gpg-agent socket basename. Linux sun_path is 108 bytes including NUL.
_LONGEST_SOCKET = "S.gpg-agent.browser"
_SOCKET_CAP = 106
_LINK_PREFIX = "as-gpg-"


def bindable_home(gnupg: Path) -> Path:
    """Path to use as GNUPGHOME / --homedir.

    Private keys stay in *gnupg*. On POSIX, GNUPGHOME is a short /tmp symlink
    so gpg-agent sockets fit the unix sockaddr limit.
    """

    home = Path(gnupg)
    if os.name == "nt":
        return home
    return _short_link(home)


def _socket_len(path: Path) -> int:
    return len(os.fsencode(path))


def _short_link(gnupg: Path) -> Path:
    try:
        target = gnupg.resolve()
    except OSError:
        target = gnupg
    digest = hashlib.sha256(os.fsencode(target)).hexdigest()[:16]
    link = Path("/tmp") / f"{_LINK_PREFIX}{digest}"
    if _socket_len(link / _LONGEST_SOCKET) > _SOCKET_CAP:
        return gnupg
    try:
        if link.is_symlink():
            try:
                if link.resolve() == target:
                    return link
            except OSError:
                pass
            link.unlink()
        elif link.exists():
            return gnupg
        link.symlink_to(target)
        return link
    except OSError:
        return gnupg

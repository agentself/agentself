"""GNUPGHOME helpers. Keys stay in the vault; sockets must fit unix sockaddr."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

# Longest gpg-agent socket basename. Linux sun_path is 108 bytes including NUL.
_LONGEST_SOCKET = "S.gpg-agent.browser"
_SOCKET_CAP = 106
_LINK_PREFIX = "as-gpg-"


def bindable_home(gnupg: Path) -> Path:
    """Path to use as GNUPGHOME / --homedir.

    Private keys stay in *gnupg*. GNUPGHOME is a short symlink under /tmp
    (POSIX) or TEMP (Windows) so gpg-agent sockets fit the unix sockaddr limit.
    """

    home = Path(gnupg)
    parent = Path(tempfile.gettempdir()) if os.name == "nt" else Path("/tmp")
    return _short_link(home, parent)


def _socket_len(path: Path) -> int:
    return len(os.fsencode(path))


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        if os.name != "nt":
            return False
        path.readlink()
        return True
    except OSError:
        return False


def _create_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import _winapi

        _winapi.CreateJunction(str(target), str(link))


def _short_link(gnupg: Path, parent: Path) -> Path:
    try:
        target = gnupg.resolve()
    except OSError:
        target = gnupg
    digest = hashlib.sha256(os.fsencode(target)).hexdigest()[:16]
    link = parent / f"{_LINK_PREFIX}{digest}"
    if _socket_len(link / _LONGEST_SOCKET) > _SOCKET_CAP:
        return gnupg
    try:
        if _is_link(link):
            try:
                if link.resolve() == target:
                    return link
            except OSError:
                pass
            link.unlink()
        elif link.exists():
            return gnupg
        _create_link(link, target)
        return link
    except OSError:
        return gnupg

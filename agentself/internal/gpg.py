"""GNUPGHOME helpers. Keys stay in the vault; sockets must fit unix sockaddr."""

from __future__ import annotations

import hashlib
import os
import shlex
import tempfile
from pathlib import Path

from agentself.internal.files import resolve_tool

# Longest gpg-agent socket basename. Linux sun_path is 108 bytes including NUL.
_LONGEST_SOCKET = "S.gpg-agent.browser"
_SOCKET_CAP = 106
_LINK_PREFIX = "as-gpg-"
_EXTENDED_PREFIX = "\\\\?\\"


def bindable_home(gnupg: Path) -> Path:
    """Path to use as GNUPGHOME / --homedir.

    Private keys stay in *gnupg*. GNUPGHOME is a short /tmp symlink (POSIX)
    or a TEMP directory junction (Windows; symlink fallback) so gpg-agent
    sockets fit the unix sockaddr limit.
    """

    home = Path(gnupg)
    parent = Path(tempfile.gettempdir()) if os.name == "nt" else Path("/tmp")
    return _short_link(home, parent)


def pass_env(gnupg: Path, store_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["GNUPGHOME"] = str(bindable_home(gnupg))
    env["PASSWORD_STORE_DIR"] = str(store_dir)
    env["PASSWORD_STORE_GPG_OPTS"] = "--pinentry-mode loopback --batch"
    env["GPG_TTY"] = ""
    if os.name == "nt":
        env.setdefault("NoDefaultCurrentDirectoryInExePath", "1")
    return env


def gpg_fingerprint(colon_listing: str) -> str | None:
    for line in colon_listing.splitlines():
        parts = line.split(":")
        if parts[0] == "fpr" and len(parts) > 9:
            fpr = parts[9]
            if len(fpr) >= 40 and all(ch in "0123456789abcdefABCDEF" for ch in fpr):
                return fpr
    return None


def pass_argv(argv: list[str]) -> list[str]:
    """Windows pass.sh under Git bash with native gpg first on PATH.

    Missing bash, pass.sh, or gpg keeps *argv* unchanged.
    """

    parts = [str(part) for part in argv]
    if os.name != "nt" or not parts:
        return parts
    if Path(parts[0]).stem.lower() != "pass":
        return parts
    return _windows_pass_argv(parts[1:]) or parts


def _socket_len(path: Path) -> int:
    return len(os.fsencode(path))


def _strip_extended(path: Path) -> Path:
    text = str(path)
    if not text.startswith(_EXTENDED_PREFIX):
        return Path(text)
    rest = text.removeprefix(_EXTENDED_PREFIX)
    if rest.startswith("UNC\\"):
        rest = "\\\\" + rest.removeprefix("UNC\\")
    return Path(rest)


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
    target = _strip_extended(target)
    if os.name != "nt":
        link.symlink_to(target)
        return
    try:
        import _winapi

        create_junction = getattr(_winapi, "CreateJunction", None)
        if create_junction is not None:
            create_junction(str(target), str(link))
            return
    except (OSError, ImportError):
        pass
    link.symlink_to(target, target_is_directory=True)


def _short_link(gnupg: Path, parent: Path) -> Path:
    try:
        target = _strip_extended(gnupg.resolve())
    except OSError:
        target = _strip_extended(gnupg)
    digest = hashlib.sha256(os.fsencode(target)).hexdigest()[:16]
    link = parent / f"{_LINK_PREFIX}{digest}"
    if _socket_len(link / _LONGEST_SOCKET) > _SOCKET_CAP:
        return gnupg
    try:
        if _is_link(link):
            try:
                same = _strip_extended(link.resolve()) == target
            except OSError:
                same = False
            if same and not (os.name == "nt" and link.is_symlink()):
                return link
            link.unlink()
        elif link.exists():
            return gnupg
        _create_link(link, target)
        return link
    except OSError:
        return gnupg


def _to_msys(path: Path) -> str:
    text = str(_strip_extended(path))
    if len(text) >= 2 and text[1] == ":":
        return "/" + text[0].lower() + text[2:].replace("\\", "/")
    return text.replace("\\", "/")


def _git_bash() -> Path | None:
    roots: list[Path] = []
    for key in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        raw = os.environ.get(key, "").strip()
        if raw:
            roots.append(Path(raw))
    roots.extend((Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")))
    seen: set[str] = set()
    for root in roots:
        bash = root / "Git" / "bin" / "bash.exe"
        mark = os.path.normcase(str(bash))
        if mark in seen:
            continue
        seen.add(mark)
        try:
            if bash.is_file():
                return bash
        except OSError:
            continue
    return None


def _windows_pass_argv(args: list[str]) -> list[str] | None:
    bash = _git_bash()
    if bash is None:
        return None
    try:
        script = Path(resolve_tool("pass")).with_name("pass.sh")
        gpg = Path(resolve_tool("gpg"))
        if not script.is_file() or not gpg.is_file():
            return None
    except OSError:
        return None
    gpg_dir = shlex.quote(_to_msys(gpg.parent))
    script_msys = shlex.quote(_to_msys(script))
    extra = " ".join(shlex.quote(arg) for arg in args)
    inner = f"export PATH={gpg_dir}:$PATH; exec {script_msys} {extra}"
    return [str(bash), "-c", inner]

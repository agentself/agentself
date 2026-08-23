from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_LOCAL = threading.local()

LOCK_NAME = "vault.lock"


class IdentityBusy(Exception):
    """Another process or thread holds the vault lock past the wait."""

    def __init__(self) -> None:
        super().__init__("identity directory busy")


def resolve_tool(name: str) -> str:
    """Absolute PATH hit, skipping the current directory (Windows cwd search)."""

    raw = str(name or "").strip()
    if not raw:
        return raw
    given = Path(raw)
    if given.is_absolute() or len(given.parts) > 1:
        return raw
    try:
        cwd = Path.cwd().resolve()
    except OSError:
        cwd = None
    names = [raw]
    if os.name == "nt" and not raw.lower().endswith((".exe", ".bat", ".cmd")):
        names.append(raw + ".exe")
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder:
            continue
        base = Path(folder)
        try:
            if cwd is not None and base.resolve() == cwd:
                continue
        except OSError:
            continue
        for cand in names:
            hit = base / cand
            try:
                if hit.is_file():
                    return str(hit)
            except OSError:
                continue
    return raw


def have_host_tool(name: str) -> bool:
    """True when resolve_tool found a PATH file. Cwd hits are not installed tools."""

    path = Path(resolve_tool(name))
    if not path.is_absolute() and len(path.parts) < 2:
        return False
    try:
        return path.is_file()
    except OSError:
        return False


def host_env(env: Mapping[str, str] | None = None) -> dict[str, str] | None:
    """Copy env; on Windows, refuse cwd as the executable search path."""

    env_map = None if env is None else dict(env)
    if os.name == "nt":
        env_map = os.environ.copy() if env_map is None else env_map
        env_map.setdefault("NoDefaultCurrentDirectoryInExePath", "1")
    return env_map


def run_resolved(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    stdin: bytes | None = None,
    timeout: float = 30,
) -> subprocess.CompletedProcess[bytes]:
    cmd = list(argv)
    if cmd:
        cmd[0] = resolve_tool(str(cmd[0]))
    return subprocess.run(
        cmd,
        input=stdin,
        capture_output=True,
        env=host_env(env),
        timeout=timeout,
        check=False,
    )


def identity_home(root: Path, identity_id: str) -> Path:
    return Path(root) / "identities" / identity_id


def secrets_home(root: Path, identity_id: str) -> Path:
    return identity_home(root, identity_id) / "secrets"


def ensure_private_dir(path: Path) -> Path:
    """mkdir 0o700 and chmod even when the directory already existed."""

    folder = Path(path)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(folder, 0o700)
    except OSError:
        pass
    return folder


def shred_unlink(path: Path | str) -> None:
    """Overwrite a file with zeros, then unlink. Never follows a symlink."""

    dest = Path(path)
    try:
        if dest.is_symlink():
            dest.unlink()
            return
    except OSError:
        return
    name = str(dest)
    try:
        size = os.path.getsize(name)
    except OSError:
        size = 0
    if size > 0:
        try:
            flags = os.O_WRONLY
            nofollow = getattr(os, "O_NOFOLLOW", 0)
            if nofollow:
                flags |= nofollow
            fd = os.open(name, flags)
            try:
                zeros = b"\x00" * min(size, 65536)
                remaining = size
                while remaining > 0:
                    wrote = os.write(fd, zeros[:remaining])
                    if wrote <= 0:
                        break
                    remaining -= wrote
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass
    try:
        os.unlink(name)
    except OSError:
        pass


def atomic_write(
    path: Path, data: bytes, *, mode: int = 0o600, private_dir: bool = True
) -> None:
    """Replace path with data, or leave the previous bytes if this crashes."""

    dest = Path(path)
    if private_dir:
        ensure_private_dir(dest.parent)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=dest.name + ".", suffix=".tmp", dir=dest.parent
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, dest)
        try:
            os.chmod(dest, mode)
        except OSError:
            pass
        _fsync_dir(dest.parent)
    except Exception:
        shred_unlink(tmp_name)
        raise


def atomic_write_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    atomic_write(path, text.encode("utf-8"), mode=mode)


@contextmanager
def exclusive(root: Path, *, timeout: float = 30.0) -> Iterator[None]:
    """Process-wide exclusive lock. Reentrant on the same thread."""

    base = ensure_private_dir(root)
    lock_path = base / LOCK_NAME
    key = str(lock_path.resolve(strict=False))
    held = getattr(_LOCAL, "held", None)
    if held is None:
        _LOCAL.held = held = {}
    depth = held.get(key, 0)
    if depth:
        held[key] = depth + 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    tlock = _thread_lock(key)
    deadline = time.monotonic() + max(0.0, timeout)
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not tlock.acquire(timeout=remaining):
        raise IdentityBusy()
    fd: int | None = None
    try:
        remaining = deadline - time.monotonic()
        fd = _lock_file(lock_path, remaining)
        held[key] = 1
        yield
    finally:
        held[key] = 0
        if fd is not None:
            _unlock_file(fd)
        tlock.release()


def _thread_lock(key: str) -> threading.RLock:
    with _GUARD:
        if key not in _THREAD_LOCKS:
            _THREAD_LOCKS[key] = threading.RLock()
        return _THREAD_LOCKS[key]


def _lock_file(path: Path, timeout: float) -> int:
    ensure_private_dir(path.parent)
    if path.is_symlink():
        raise IdentityBusy()
    flags = os.O_RDWR | os.O_CREAT
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    fd = os.open(str(path), flags, 0o600)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\n")  # msvcrt.locking needs at least one byte
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            if _try_lock(fd):
                try:
                    os.chmod(str(path), 0o600)
                except OSError:
                    pass
                return fd
            if time.monotonic() >= deadline:
                raise IdentityBusy()
            time.sleep(0.05)
    except Exception:
        os.close(fd)
        raise


def _try_lock(fd: int) -> bool:
    if os.name == "nt":
        import msvcrt

        try:
            getattr(msvcrt, "locking")(fd, getattr(msvcrt, "LK_NBLCK"), 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        getattr(fcntl, "flock")(
            fd, getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB")
        )
        return True
    except OSError:
        return False


def _unlock_file(fd: int) -> None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            getattr(msvcrt, "locking")(fd, getattr(msvcrt, "LK_UNLCK"), 1)
        else:
            import fcntl

            getattr(fcntl, "flock")(fd, getattr(fcntl, "LOCK_UN"))
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def _fsync_dir(folder: Path) -> None:
    try:
        dir_fd = os.open(str(folder), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)

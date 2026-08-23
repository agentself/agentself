"""Host tools dir. Not the vault."""

from __future__ import annotations

import hashlib
import io
import os
import platform
import shutil
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from agentself import __version__
from agentself.host import ENV_FETCH_TOOLS, ENV_TOOLS_DIR

AGE_VERSION = "v1.3.1"
SOPS_VERSION = "v3.13.3"
INSTALLABLE_TOOLS = frozenset({"age", "age-keygen", "sops"})

_AGE_BASE = f"https://github.com/FiloSottile/age/releases/download/{AGE_VERSION}/"
_SOPS_BASE = f"https://github.com/getsops/sops/releases/download/{SOPS_VERSION}/"

# SHA-256 of the official release assets. age has no checksums.txt; hashed here.
_DIGESTS = {
    "age-v1.3.1-linux-amd64.tar.gz": "bdc69c09cbdd6cf8b1f333d372a1f58247b3a33146406333e30c0f26e8f51377",
    "age-v1.3.1-linux-arm64.tar.gz": "c6878a324421b69e3e20b00ba17c04bc5c6dab0030cfe55bf8f68fa8d9e9093a",
    "age-v1.3.1-darwin-amd64.tar.gz": "2b233301ad21ab7b1eabd9ae1198a164005fa4928fcdd745d47c39f8593209d7",
    "age-v1.3.1-darwin-arm64.tar.gz": "01120ea2cbf0463d4c6bd767f99f3271bbed1cdc8a9aa718a76ba1fe4f01998b",
    "age-v1.3.1-windows-amd64.zip": "c56e8ce22f7e80cb85ad946cc82d198767b056366201d3e1a2b93d865be38154",
    "sops-v3.13.3.linux.amd64": "e5bec3346a873ae91d871550f3e698c1aad962aff462a080e40f25fde17fef6b",
    "sops-v3.13.3.linux.arm64": "53b0abacd38ef1b12a66d6c100956691b9cefce018d91f81e73ddf7438b94d77",
    "sops-v3.13.3.darwin.amd64": "42162d5cef10b74fcf80a045a70e658d7ce6e63d6ea1be6f347e44015714468d",
    "sops-v3.13.3.darwin.arm64": "b97c0d434aab577dc40310e8d22ff9e45eef4c80638ab978daae9b4681c59286",
    "sops-v3.13.3.amd64.exe": "a4a9a398858fe8b2ef72d9686d930bf7c5cece9be74ad83ac3b53cfdd70e6b1c",
    "sops-v3.13.3.arm64.exe": "7f936a0d08edbeba3eb8f50bad1fe71eba83a140a80dfd17051f3bdb4facb08a",
}

_AGE_FILE = {
    ("linux", "amd64"): "age-v1.3.1-linux-amd64.tar.gz",
    ("linux", "arm64"): "age-v1.3.1-linux-arm64.tar.gz",
    ("darwin", "amd64"): "age-v1.3.1-darwin-amd64.tar.gz",
    ("darwin", "arm64"): "age-v1.3.1-darwin-arm64.tar.gz",
    ("windows", "amd64"): "age-v1.3.1-windows-amd64.zip",
}

_SOPS_FILE = {
    ("linux", "amd64"): "sops-v3.13.3.linux.amd64",
    ("linux", "arm64"): "sops-v3.13.3.linux.arm64",
    ("darwin", "amd64"): "sops-v3.13.3.darwin.amd64",
    ("darwin", "arm64"): "sops-v3.13.3.darwin.arm64",
    ("windows", "amd64"): "sops-v3.13.3.amd64.exe",
    ("windows", "arm64"): "sops-v3.13.3.arm64.exe",
}


class HostToolError(Exception):
    """Message never contains a secret."""


def tools_dir() -> Path:
    override = os.environ.get(ENV_TOOLS_DIR, "").strip()
    if override:
        return Path(override)
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(root) if root else Path.home() / "AppData" / "Local"
    else:
        root = os.environ.get("XDG_DATA_HOME", "").strip()
        base = Path(root) if root else Path.home() / ".local" / "share"
    return base / "agentself" / "bin"


def fetch_enabled() -> bool:
    raw = os.environ.get(ENV_FETCH_TOOLS, "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def ensure_host_tools(*, fetch: bool = True) -> None:
    dest = tools_dir()
    _prepend_path(dest)
    need_age = shutil.which("age-keygen") is None
    need_sops = shutil.which("sops") is None
    if not need_age and not need_sops:
        return
    if not fetch or not fetch_enabled():
        return
    kind = _host_kind()
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    if need_age:
        _install_age(dest, kind)
    if need_sops:
        _install_sops(dest, kind)
    _prepend_path(dest)


def _host_kind() -> tuple[str, str]:
    system = platform.system().lower()
    if system.startswith("win"):
        system = "windows"
    elif system not in ("linux", "darwin"):
        raise HostToolError("unsupported host")
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        raise HostToolError("unsupported host")
    return system, arch


def _prepend_path(dest: Path) -> None:
    if not dest.is_dir():
        return
    current = os.environ.get("PATH", "")
    prefix = str(dest)
    parts = current.split(os.pathsep) if current else []
    if parts and parts[0] == prefix:
        return
    os.environ["PATH"] = prefix + os.pathsep + current if current else prefix


def _install_age(dest: Path, kind: tuple[str, str]) -> None:
    name = _AGE_FILE.get(kind)
    if name is None:
        raise HostToolError("age not available for this host")
    blob = _verified(_AGE_BASE + name, name)
    windows = kind[0] == "windows"
    wanted = {"age.exe", "age-keygen.exe"} if windows else {"age", "age-keygen"}
    staging = dest / ".age-extract"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(mode=0o700)
    try:
        _unpack(blob, name, staging)
        found = {
            path.name: path
            for path in staging.rglob("*")
            if path.is_file() and path.name in wanted
        }
        if set(found) != wanted:
            raise HostToolError("age archive missing binaries")
        for filename, src in found.items():
            _place(src.read_bytes(), dest / filename)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _install_sops(dest: Path, kind: tuple[str, str]) -> None:
    name = _SOPS_FILE.get(kind)
    if name is None:
        raise HostToolError("sops not available for this host")
    blob = _verified(_SOPS_BASE + name, name)
    windows = kind[0] == "windows"
    _place(blob, dest / _bin_name("sops", windows))


def _bin_name(name: str, windows: bool) -> str:
    return f"{name}.exe" if windows else name


def _unpack(blob: bytes, filename: str, dest: Path) -> None:
    buf = io.BytesIO(blob)
    if filename.endswith(".zip"):
        with zipfile.ZipFile(buf) as archive:
            archive.extractall(dest)
        return
    with tarfile.open(fileobj=buf, mode="r:gz") as archive:
        try:
            archive.extractall(dest, filter="data")
        except TypeError:
            archive.extractall(dest)


def _place(blob: bytes, dest: Path) -> None:
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(blob)
    _finish_file(tmp, dest)


def _finish_file(tmp: Path, dest: Path) -> None:
    try:
        os.chmod(tmp, 0o755)
    except OSError:
        pass
    os.replace(tmp, dest)


def _verified(url: str, filename: str) -> bytes:
    expected = _DIGESTS.get(filename)
    if expected is None:
        raise HostToolError("host tool checksum mismatch")
    blob = _http_get(url)
    digest = hashlib.sha256(blob).hexdigest()
    if digest != expected:
        raise HostToolError("host tool checksum mismatch")
    return blob


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"agentself/{__version__}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except (OSError, urllib.error.URLError) as exc:
        raise HostToolError("could not fetch host tools") from exc

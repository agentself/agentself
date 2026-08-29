from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

from agentself import __version__
from agentself.bind import public_recipient
from agentself.cli.io import load_value_file
from agentself.cli.outcomes import CliOutcome, CliSuccess
from agentself.cli.runtime import (
    INSTALL_TOOLS_NEXT,
    PASS_TOOLS_NEXT,
    PASS_TOOLS_REASON,
    bind_error,
    client,
    diagnose_tools,
    fail,
    fail_missing_tool,
    identity_fail,
    init_next,
    not_initialized,
    registry_store_binding,
    require_bind,
    runtime_paths,
    status_json,
    store_fail,
    store_from_registry,
    store_reason,
    with_identity_dir,
)
from agentself.host import CHANNELS, ENV_IDENTITY_DIR, ENV_IDENTITY_ID, UnknownBind
from agentself.internal.custody.errors import (
    HostToolMissing,
    ProtectedName,
    Refused,
    StoreFailure,
    UnboundCaller,
)
from agentself.internal.eoa import parse_secp256k1_hex
from agentself.internal.files import (
    LOCK_NAME,
    IdentityBusy,
    exclusive,
    have_host_tool,
)
from agentself.internal.names import WALLET_KEY_NAME, require_safe_token
from agentself.local import (
    DEFAULT_IDENTITY,
    IdentityStateError,
    config_path,
    ensure_age_key,
    load_config,
    merge_config,
    redact_secrets,
    require_supported_formats,
    resolve_age_key_file,
    resolve_setting,
)

_SKILL_TARGETS = {
    "claude": Path(".claude") / "skills" / "agentself",
    "agents": Path(".agents") / "skills" / "agentself",
}


def show_identity(args, vault: Path) -> CliOutcome:
    view = client(vault).identity()
    return CliSuccess(status_json(view, vault))


def init_identity(args, vault: Path) -> CliOutcome:
    try:
        require_supported_formats(vault)
        cfg = load_config(vault)
        store = args.store or CHANNELS["store"].default
        refused = require_bind(args, "store", store)
        if refused is not None:
            return refused
        backends: dict[str, str] = {}
        for channel, spec in CHANNELS.items():
            if spec.env is None:
                continue
            value = resolve_setting(
                vault,
                spec.config_key or f"{channel}_backend",
                spec.env or "",
                spec.default,
                getattr(args, spec.name, None),
            )
            refused = require_bind(args, channel, value)
            if refused is not None:
                return refused
            backends[channel] = value
        email_backend = backends["email"]
        wallet_backend = backends["wallet"]
        try:
            identity_id = _init_identity_id(vault, args)
        except ValueError as exc:
            detail = str(exc).strip()
            if detail.startswith("invalid identity id"):
                return fail(
                    args,
                    2,
                    "refused",
                    "invalid identity id; use letters, digits, dot, underscore, or hyphen",
                    nxt="agentself init --help",
                )
            raise
        current_id = (cfg.get("identity_id") or "").strip()
        if current_id and current_id != identity_id:
            return fail(
                args,
                2,
                "refused",
                "identity already initialized",
                nxt=init_next(args),
            )
        initialized = bool(cfg.get("identity_id") and cfg.get("age_key_file"))
        if initialized:
            blocked = _init_mutation_refused(
                vault,
                args,
                cfg,
                identity_id,
                wallet_backend,
                email_backend,
                store,
            )
            if blocked is not None:
                return blocked
        key = ensure_age_key(vault, identity_id)
        cfg = load_config(vault)
        age_key_file = _age_key_rel(vault, identity_id, cfg)
        identity_fields = {
            "identity_id": identity_id,
            "age_key_file": age_key_file,
        }
        backend_fields = {
            "email_backend": email_backend,
            "wallet_backend": wallet_backend,
        }
        if not cfg.get("identity_id") or not cfg.get("age_key_file"):
            merge_config(vault, identity_fields)
        recipient = public_recipient(str(key))
        access = client(
            vault,
            email_backend=email_backend,
            wallet_backend=wallet_backend,
        )
        access.init(store)
        sealed = _seal_init_wallet_key(access, args)
        if sealed is not None:
            return sealed
        addr = access.wallet_address()
        merge_config(vault, {**identity_fields, **backend_fields})
        return CliSuccess(
            {
                "id": identity_id,
                "recipient": recipient,
                "address": addr,
                "wallet_backend": wallet_backend,
                "email_backend": email_backend,
            }
        )
    except UnboundCaller:
        return not_initialized(args)
    except IdentityStateError as exc:
        return identity_fail(args, exc)
    except UnknownBind as exc:
        return bind_error(args, exc)
    except ValueError:
        return fail(args, 2, "refused")
    except HostToolMissing as exc:
        return fail_missing_tool(args, exc, vault)
    except StoreFailure as exc:
        return store_fail(args, exc)
    except FileNotFoundError:
        return fail(
            args,
            1,
            "error",
            "age not on PATH",
            nxt=INSTALL_TOOLS_NEXT,
        )
    except RuntimeError as exc:
        detail = redact_secrets(str(exc).strip()) or "error"
        return fail(args, 1, "error", detail)


def diagnose_host(args, vault: Path) -> CliOutcome:
    try:
        cfg = load_config(vault)
        initialized = config_path(vault).is_file()
        store_name = store_from_registry(vault, cfg) or CHANNELS["store"].default
    except IdentityStateError as exc:
        return identity_fail(args, exc)
    python = sys.version.split()[0]
    wallet_backend = email_backend = store_backend = None
    ready = {"wallet": False, "email": False, "store": False}
    problems: list[tuple[str, str]] = []
    if initialized:
        wallet_backend = (cfg.get("wallet_backend") or "").strip() or None
        email_backend = (cfg.get("email_backend") or "").strip() or None
        store_backend = store_name
        problems, ready = _diagnose_identity(vault, cfg, store_name, args)
    paths = runtime_paths()
    tools = diagnose_tools(store_name)
    payload: dict[str, object] = {
        "initialized": initialized,
        "ready": ready,
        "version": __version__,
        "python": python,
        "identity_dir": str(vault),
        "tools": tools,
        "wallet_backend": wallet_backend,
        "email_backend": email_backend,
        "store_backend": store_backend,
        **paths,
    }
    nxt = _diagnose_next(args, initialized, ready, problems)
    payload["next"] = nxt
    if problems:
        extra = {
            **payload,
            "ok": False,
            "problems": [item[0] for item in problems],
        }
        return fail(
            args,
            1,
            "error",
            problems[0][0],
            nxt=nxt,
            extra=extra,
        )
    return CliSuccess(payload)


def backup_identity(args, vault: Path) -> CliOutcome:
    return _backup_restore(vault, args)


def restore_identity(args, vault: Path) -> CliOutcome:
    return _backup_restore(vault, args)


def install_components(args, _vault: Path) -> CliOutcome:
    skills = args.skills
    tools = args.tools
    if skills is None and not tools:
        return fail(
            args,
            2,
            "refused",
            "need --skills or --tools",
            nxt="agentself install --skills",
        )
    payload: dict[str, object] = {}
    paths: list[str] = []
    if tools:
        tool_err = _install_tools(args)
        if tool_err is not None:
            return tool_err
        from agentself.internal.host_tools import tools_dir

        payload["path"] = str(tools_dir())
    if skills is not None:
        paths, skill_err = _install_skills(args, skills)
        if skill_err is not None:
            return skill_err
        payload["paths"] = paths
    return CliSuccess(payload)


def _init_identity_id(vault: Path, args) -> str:
    flagged = (args.identity_id or "").strip()
    if flagged:
        return require_safe_token(flagged, "identity id")
    existing = load_config(vault).get("identity_id", "").strip()
    if existing:
        return require_safe_token(existing, "identity id")
    env_id = os.environ.get(ENV_IDENTITY_ID, "").strip()
    if env_id:
        return require_safe_token(env_id, "identity id")
    return DEFAULT_IDENTITY


def _init_mutation_refused(
    vault: Path,
    args,
    cfg: dict[str, str],
    identity_id: str,
    wallet_backend: str,
    email_backend: str,
    store: str,
) -> CliOutcome | None:
    current_id = (cfg.get("identity_id") or "").strip()
    current_wallet = (cfg.get("wallet_backend") or "").strip()
    current_email = (cfg.get("email_backend") or "").strip()
    recorded = registry_store_binding(vault, current_id) if current_id else None
    changing = (
        (current_wallet and current_wallet != wallet_backend)
        or (current_email and current_email != email_backend)
        or (recorded and recorded != store)
    )
    if not changing:
        return None
    if args.force:
        return None
    return fail(
        args,
        2,
        "refused",
        "identity already initialized",
        nxt="agentself init --force",
    )


def _age_key_rel(vault: Path, identity_id: str, cfg: dict[str, str]) -> str:
    stored = (cfg.get("age_key_file") or "").strip()
    resolved = resolve_age_key_file(vault, stored) if stored else ""
    if resolved and Path(resolved).is_file():
        return stored
    return f"identities/{identity_id}/agent.agekey"


def _read_wallet_key_file(path: str) -> tuple[str | None, str | None]:
    try:
        return load_value_file(path), None
    except (OSError, UnicodeDecodeError):
        return None, "no_key"


def _seal_init_wallet_key(access, args) -> CliOutcome | None:
    path = (getattr(args, "wallet_key_file", None) or "").strip()
    if not path:
        return None
    raw, err = _read_wallet_key_file(path)
    parsed = parse_secp256k1_hex(raw or "") if err is None else None
    if err is not None or parsed is None:
        return fail(
            args,
            2,
            "refused",
            "no_key",
            nxt="agentself init --help",
        )
    names = access.list()
    if WALLET_KEY_NAME in names:
        if getattr(args, "unsafe", False):
            access.update(WALLET_KEY_NAME, parsed, unsafe=True)
            return None
        try:
            access.create(WALLET_KEY_NAME, parsed)
        except ProtectedName as exc:
            return fail(
                args,
                2,
                "refused",
                str(exc),
                nxt="agentself secret update NAME --unsafe",
            )
        except Refused as exc:
            detail = str(exc).strip() or f"{WALLET_KEY_NAME} is protected"
            if detail == "refused":
                detail = f"{WALLET_KEY_NAME} is protected"
            return fail(
                args,
                2,
                "refused",
                detail,
                nxt="agentself secret update NAME --unsafe",
            )
        return None
    access.create(WALLET_KEY_NAME, parsed)
    return None


def _diagnose_next(
    args,
    initialized: bool,
    ready: dict[str, bool],
    problems: list[tuple[str, str]],
) -> str:
    if problems:
        return problems[0][1]
    if not initialized:
        return init_next(args)
    if not ready.get("email"):
        return with_identity_dir(args, "email connect")
    return with_identity_dir(args, "email receive")


def _required_wallet_runtime(wallet_backend: str) -> list[tuple[str, str]]:
    from agentself.host import bind_of

    bind = bind_of("wallet", wallet_backend)
    if bind is None:
        return []
    missing: list[tuple[str, str]] = []
    for option in bind.options:
        if not option.get("required"):
            continue
        source = str(option.get("source") or "").strip()
        if not source:
            continue
        if not os.environ.get(source, "").strip():
            missing.append((f"{source} is required", f"set {source}"))
    return missing


def _diagnose_identity(
    vault: Path, cfg: dict[str, str], store_name: str, args
) -> tuple[list[tuple[str, str]], dict[str, bool]]:
    from agentself.host import unknown_bind

    problems: list[tuple[str, str]] = []
    ready = {"wallet": False, "email": False, "store": False}
    checks = (
        ("wallet", (cfg.get("wallet_backend") or "").strip()),
        ("email", (cfg.get("email_backend") or "").strip()),
        ("store", store_name),
    )
    for channel, value in checks:
        if value and unknown_bind(channel, value):
            problems.append(
                (str(UnknownBind(channel, value)), f"agentself backends {channel}")
            )
    key_file = resolve_age_key_file(vault, cfg.get("age_key_file", ""))
    if not key_file or not Path(key_file).is_file():
        problems.append(("age key file is missing", init_next(args)))
    tools = diagnose_tools(store_name)
    if store_name == "pass" and not (tools.get("gpg") and tools.get("pass")):
        problems.append((PASS_TOOLS_REASON, PASS_TOOLS_NEXT))
    if problems:
        return problems, ready
    try:
        access = client(vault)
        names = access.list()
    except UnboundCaller:
        problems.append(("age key file is not usable", init_next(args)))
        return problems, ready
    except UnknownBind as exc:
        problems.append((str(exc), f"agentself backends {exc.channel}"))
        return problems, ready
    except StoreFailure as exc:
        problems.append((store_reason(exc), "agentself secret list"))
        return problems, ready
    except Exception:
        problems.append(("identity is not usable", init_next(args)))
        return problems, ready
    ready["store"] = True
    ready["email"] = "email.address" in names
    try:
        status = access.wallet_material_status()
    except StoreFailure as exc:
        problems.append((store_reason(exc), "agentself secret list"))
        return problems, ready
    except Exception:
        problems.append(("identity is not usable", init_next(args)))
        return problems, ready
    if not status.get("ready"):
        missing = str(status.get("missing") or "wallet material")
        problems.append((f"{missing} is missing", init_next(args)))
        return problems, ready
    wallet_backend = (cfg.get("wallet_backend") or "").strip()
    rpc_missing = _required_wallet_runtime(wallet_backend)
    if rpc_missing:
        problems.extend(rpc_missing)
        return problems, ready
    ready["wallet"] = True
    return problems, ready


def _bundled_skill() -> Path:
    return (
        Path(__file__).resolve().parent.parent.parent
        / "skills"
        / "agentself"
        / "SKILL.md"
    )


def _copy_skill_tree(src_dir: Path, dest_dir: Path) -> list[str]:
    """Copy packaged skill files without following links in either tree."""
    # Path sort is case-insensitive on Windows; posix keeps SKILL.md first.
    entries = sorted(
        src_dir.rglob("*"), key=lambda path: path.relative_to(src_dir).as_posix()
    )
    if not entries:
        raise OSError("skill not packaged")
    dest_dir.mkdir(parents=True, exist_ok=True)
    if dest_dir.is_symlink():
        raise OSError("refusing linked skill destination")
    copied: list[str] = []
    for src in entries:
        if src.is_symlink():
            raise OSError("refusing linked packaged skill path")
        relative = src.relative_to(src_dir)
        dest = dest_dir / relative
        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            if dest.is_symlink():
                raise OSError("refusing linked skill destination")
            continue
        if not src.is_file():
            raise OSError("refusing non-file packaged skill path")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink():
            raise OSError("refusing linked skill destination")
        shutil.copyfile(src, dest)
        copied.append(str(dest))
    if str(dest_dir / "SKILL.md") not in copied:
        raise OSError("skill not packaged")
    return copied


def _install_tools(args) -> CliOutcome | None:
    from agentself.internal.host_tools import (
        HostToolError,
        ensure_host_tools,
        fetch_enabled,
    )

    try:
        ensure_host_tools(fetch=True)
    except HostToolError as exc:
        return fail(args, 1, "error", str(exc), nxt=INSTALL_TOOLS_NEXT)
    missing = [
        label
        for cmd, label in (("age-keygen", "age"), ("sops", "sops"))
        if not have_host_tool(cmd)
    ]
    if not missing:
        return None
    reason = (
        "host tool fetch is disabled"
        if not fetch_enabled()
        else f"{missing[0]} not on PATH"
    )
    return fail(args, 1, "error", reason, nxt=INSTALL_TOOLS_NEXT)


def _install_skills(args, requested: str) -> tuple[list[str], CliOutcome | None]:
    target = (requested or "").strip().lower()
    if target not in _SKILL_TARGETS:
        return [], fail(
            args,
            2,
            "refused",
            "unknown skills target",
            nxt="agentself install --help",
        )
    src = _bundled_skill()
    if not src.is_file():
        return [], fail(
            args,
            1,
            "error",
            "skill not packaged",
            nxt="agentself --help",
        )
    rel = _SKILL_TARGETS[target]
    dest_root = Path.home() if args.global_install else Path.cwd()
    dest_dir = dest_root / rel
    try:
        paths = _copy_skill_tree(src.parent, dest_dir)
    except OSError as exc:
        detail = str(exc).strip() or "could not install skill"
        return [], fail(args, 1, "error", detail)
    return paths, None


def _backup_restore(vault: Path, args) -> CliOutcome:
    src = vault if args.command == "backup" else Path(args.path)
    dest = Path(args.path) if args.command == "backup" else vault
    if args.command == "restore":
        flagged = (getattr(args, "identity_dir", None) or "").strip()
        env_dir = os.environ.get(ENV_IDENTITY_DIR, "").strip()
        if not flagged and not env_dir and not config_path(vault).is_file():
            return fail(
                args,
                2,
                "refused",
                "identity directory is missing",
                nxt="agentself --identity-dir PATH restore PATH",
            )
    try:
        try:
            with exclusive(vault):
                _copy_identity_dir(src, dest, force=args.force)
        except IdentityBusy as exc:
            raise IdentityStateError("identity directory busy") from exc
    except IdentityStateError as exc:
        return fail(
            args,
            1,
            "error",
            str(exc),
            nxt=f"agentself {args.command} --help",
        )
    except OSError as exc:
        detail = str(exc).strip() or "copy failed"
        return fail(args, 1, "error", detail)
    return CliSuccess({"path": str(dest)})


def _ignore_identity_junk(directory: str, names: list[str]) -> set[str]:
    del directory
    return {name for name in names if name.endswith(".tmp") or name == LOCK_NAME}


def _copy_identity_file(src: str, dest: str, *, follow_symlinks: bool = True) -> str:
    del follow_symlinks
    try:
        mode = os.lstat(src).st_mode
    except OSError:
        return dest
    if stat.S_ISLNK(mode):
        if os.path.lexists(dest):
            os.unlink(dest)
        os.symlink(os.readlink(src), dest)
        return dest
    if not stat.S_ISREG(mode):
        return dest
    shutil.copy2(src, dest, follow_symlinks=False)
    return dest


def _install_staged(staging: Path, dest: Path) -> None:
    if not dest.exists():
        os.rename(staging, dest)
        return
    prev = dest.with_name(dest.name + ".agentself-prev")
    if prev.exists():
        shutil.rmtree(prev)
    try:
        os.rename(dest, prev)
    except OSError:
        _replace_tree_contents(staging, dest)
        return
    try:
        os.rename(staging, dest)
    except OSError:
        os.rename(prev, dest)
        raise
    shutil.rmtree(prev, ignore_errors=True)


def _replace_tree_contents(staging: Path, dest: Path) -> None:
    keep = {LOCK_NAME}
    prev = dest.with_name(dest.name + ".agentself-prev")
    if prev.exists():
        shutil.rmtree(prev)
    prev.mkdir(mode=0o700)
    done = False
    try:
        for child in list(dest.iterdir()):
            if child.name in keep:
                continue
            os.rename(child, prev / child.name)
        for child in list(staging.iterdir()):
            if child.name in keep:
                continue
            os.rename(child, dest / child.name)
        done = True
    except Exception:
        leftover = [path for path in dest.iterdir() if path.name not in keep]
        if not leftover:
            for child in list(prev.iterdir()):
                os.rename(child, dest / child.name)
        raise
    finally:
        if done:
            shutil.rmtree(prev, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)


def _copy_identity_dir(src: Path, dest: Path, *, force: bool) -> None:
    if not src.is_dir():
        raise IdentityStateError("identity directory is missing")
    try:
        src_r = src.resolve()
        dest_r = dest.resolve()
    except OSError as exc:
        raise IdentityStateError("cannot read path") from exc
    if dest_r == src_r:
        raise IdentityStateError("destination is the identity directory")
    if dest_r.is_relative_to(src_r):
        raise IdentityStateError("destination is inside the identity directory")
    if src_r.is_relative_to(dest_r):
        raise IdentityStateError("destination contains the identity directory")
    if not (src / "config.json").is_file():
        raise IdentityStateError("identity directory is missing")
    if dest.exists():
        if dest.is_file():
            raise IdentityStateError("destination exists")
        try:
            meaningful = [path for path in dest.iterdir() if path.name != LOCK_NAME]
        except OSError as exc:
            raise IdentityStateError("cannot read destination") from exc
        if meaningful and not force:
            raise IdentityStateError("destination is not empty")
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.with_name(dest.name + ".agentself-staging")
    try:
        staging_r = staging.resolve()
    except OSError as exc:
        raise IdentityStateError("cannot read path") from exc
    if staging_r == src_r or staging_r.is_relative_to(src_r):
        raise IdentityStateError("destination is inside the identity directory")
    if staging.exists():
        shutil.rmtree(staging)
    try:
        shutil.copytree(
            src,
            staging,
            copy_function=_copy_identity_file,
            ignore=_ignore_identity_junk,
            symlinks=True,
        )
        _install_staged(staging, dest)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

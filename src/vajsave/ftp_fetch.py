"""Pull handheld saves over FTP into a local, scannable cache directory.

The pull is *atomic*: everything lands in a hidden staging directory first and
the preset's cache directory is only swapped in once the whole tree is
populated. Unchanged files are copied from the previous cache using the
``.ftp-manifest.json`` skip-list. A failed or cancelled pull never turns into
a half-populated "device", and a previously complete cache is left untouched.

The local layout mirrors the remote one, so the existing platform scanners
recognise ``<cache>/<host>/<preset>/3ds/Checkpoint/saves/...`` as a normal card.
A preset with ``target_paths`` pulls only those directories.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Tuple
from uuid import uuid4

from .ftp_manifest import load_manifest, save_manifest, should_reuse
from .remote_ftp import (
    DEFAULT_FTP_TIMEOUT,
    FtpProfile,
    RemoteFtpClient,
    RemoteFtpError,
    allow_dir_names_for,
    cache_host_component,
    redact,
    sanitize_component,
    skip_dir_names_for,
)

FTP_CACHE_DIRNAME = "ftp-cache"

# Bounded recursion so a malformed/looping server listing cannot run away.
_MAX_DEPTH = 16

# Top-level directories that never contain save files (games, updates, media).
# Skipping them prevents runaway scans over full console filesystems.
SKIP_DIR_NAMES = frozenset(
    {
        "app",
        "appmeta",
        "patch",
        "addcont",
        "license",
        "bgdl",
        "video",
        "music",
        "picture",
        "photos",
        "nintendo",
        "emummc",
        "iso",
        "games",
        "gamez",
        "ps3iso",
        "psxiso",
        "ps2iso",
        "pspiso",
        "wbfs",
        "install",
        "luma",
        "$recycle.bin",
        "system volume information",
    }
)


@dataclass
class FtpPullResult:
    """Outcome of one preset pull."""

    ok: bool
    preset_key: str
    path: Optional[Path] = None
    files: int = 0
    total_bytes: int = 0
    error: str = ""

    def __bool__(self) -> bool:
        return self.ok


def ftp_cache_root(library_root) -> Path:
    """The directory holding every preset's pulled cache."""
    return Path(library_root) / FTP_CACHE_DIRNAME


def cache_dir_in(cache_root, preset_key: str, host: object = None) -> Path:
    """Cache directory under an already-resolved ``ftp-cache`` root.

    ``host`` splits two consoles that share a preset. Omit it only for callers
    that still address the legacy flat layout.
    """
    key = sanitize_component(preset_key) or "default"
    base = Path(cache_root)
    if host is None:
        return base / key
    return base / cache_host_component(host) / key


def cache_dir_for(library_root, preset_key: str, host: object = None) -> Path:
    """The cache directory for one preset (hostile keys cannot escape)."""
    return cache_dir_in(ftp_cache_root(library_root), preset_key, host)


def _safe_local_join(base: Path, name: str) -> Path:
    """Resolve ``base / name`` and guarantee it stays under ``base``."""
    clean = sanitize_component(name)
    if clean is None:
        raise RemoteFtpError(f"不安全的缓存路径: {name!r}")
    base_resolved = base.resolve()
    candidate = (base_resolved / clean).resolve()
    if candidate != base_resolved and base_resolved not in candidate.parents:
        raise RemoteFtpError(f"缓存路径越界: {name!r}")
    return candidate


class _FtpPullCancelled(Exception):
    """Internal: cooperative cancel of an in-flight pull."""


def _throw_if_cancelled(token: object) -> None:
    if token is None:
        return
    raiser = getattr(token, "raise_if_cancelled", None)
    if callable(raiser):
        try:
            raiser()
        except _FtpPullCancelled:
            raise
        except Exception as exc:
            raise _FtpPullCancelled("已取消") from exc
    cancelled = getattr(token, "cancelled", None)
    if callable(cancelled) and cancelled():
        raise _FtpPullCancelled("已取消")


def _relative_posix(*parts: str) -> str:
    return "/".join(part for part in parts if part)


def _is_missing_path(exc: BaseException) -> bool:
    """True when a listing failed because that directory is not on the server."""
    if isinstance(exc, (KeyError, FileNotFoundError)):
        return True
    text = str(exc).lower()
    return "550" in text or "no such" in text or "not found" in text or "不存在" in text


def _remote_under(base: str, rel: str) -> str:
    """Join a card-relative ``rel`` onto the preset root."""
    parts = [part for part in str(rel or "").replace("\\", "/").split("/") if part]
    for part in parts:
        if sanitize_component(part) is None:
            raise RemoteFtpError(f"不安全的存档目录: {rel!r}")
    root = str(base or "/").replace("\\", "/").rstrip("/")
    suffix = "/".join(parts)
    if not root:
        return "/" + suffix
    return f"{root}/{suffix}"


def _local_rel(staging: Path, rel: str) -> Path:
    """The cache directory that preserves ``rel`` under ``staging``."""
    current = staging
    for part in str(rel or "").replace("\\", "/").split("/"):
        if not part:
            continue
        current = _safe_local_join(current, part)
    return current


def _cached_file(old_root: Path, rel: str) -> Optional[Path]:
    try:
        current = Path(old_root)
        for part in rel.replace("\\", "/").split("/"):
            if not part:
                continue
            current = _safe_local_join(current, part)
        return current if current.is_file() else None
    except RemoteFtpError:
        return None


def _copy_or_download(
    client,
    remote_child: str,
    target: Path,
    *,
    entry,
    old_root: Optional[Path],
    rel: str,
    recorded: Optional[dict],
) -> int:
    """Copy from the previous cache when the skip-list matches; else download."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if old_root is not None and should_reuse(entry.size, entry.modified, recorded):
        source = _cached_file(old_root, rel)
        if source is not None:
            try:
                shutil.copy2(source, target)
                return int(target.stat().st_size)
            except OSError:
                pass
    return int(client.download(remote_child, target))


def _mirror(
    client,
    remote_dir: str,
    local_dir: Path,
    depth: int = 0,
    *,
    old_root: Optional[Path] = None,
    manifest: Optional[dict] = None,
    files_out: Optional[dict] = None,
    token: object = None,
    rel: str = "",
    skip_dir_names: frozenset = frozenset(),
    allow_at_depth: Optional[Mapping[int, frozenset]] = None,
    optional: bool = False,
) -> Tuple[int, int]:
    """Recursively download ``remote_dir`` into ``local_dir``.

    Unchanged files (size + modified match the previous manifest) are copied
    from ``old_root`` instead of downloaded. Unsafe entry names are skipped,
    and every local target is re-validated to stay inside the staging tree.
    ``optional`` is only for the top call of an allowlisted root: a missing
    directory is skipped, any other listing error still fails the pull.
    """
    _throw_if_cancelled(token)
    if depth > _MAX_DEPTH:
        return (0, 0)
    try:
        entries = list(client.list_dir(remote_dir))
    except (RemoteFtpError, KeyError, FileNotFoundError) as exc:
        if optional and depth == 0 and _is_missing_path(exc):
            return (0, 0)
        raise
    files = 0
    total = 0
    recorded_files = manifest if manifest is not None else {}
    collected = files_out if files_out is not None else {}
    if entries:
        local_dir.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        _throw_if_cancelled(token)
        clean = sanitize_component(entry.name)
        if clean is None:
            continue
        if entry.is_dir and (
            clean.casefold() in SKIP_DIR_NAMES
            or entry.name.casefold() in SKIP_DIR_NAMES
            or clean.casefold() in skip_dir_names
        ):
            continue
        if entry.is_dir and allow_at_depth:
            allowed = allow_at_depth.get(depth)
            if allowed is not None and clean.casefold() not in allowed:
                continue
        target = _safe_local_join(local_dir, clean)
        remote_child = remote_dir.rstrip("/") + "/" + (entry.name if entry.name.endswith(":") else clean)
        child_rel = _relative_posix(rel, clean)
        if entry.is_dir:
            child_files, child_bytes = _mirror(
                client,
                remote_child,
                target,
                depth + 1,
                old_root=old_root,
                manifest=recorded_files,
                files_out=collected,
                token=token,
                rel=child_rel,
                skip_dir_names=skip_dir_names,
                allow_at_depth=allow_at_depth,
            )
            files += child_files
            total += child_bytes
        else:
            written = _copy_or_download(
                client,
                remote_child,
                target,
                entry=entry,
                old_root=old_root,
                rel=child_rel,
                recorded=recorded_files.get(child_rel),
            )
            collected[child_rel] = {
                "size": int(getattr(entry, "size", 0) or 0),
                "modified": str(getattr(entry, "modified", "") or ""),
            }
            total += written
            files += 1
            _throw_if_cancelled(token)
    return files, total


def _error_text(exc: BaseException, profile: FtpProfile) -> str:
    message = str(exc) or exc.__class__.__name__
    return redact(message, profile.password)


def _remove_tree(path: Path) -> None:
    try:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def pull_preset(
    profile: FtpProfile,
    cache_root,
    client_factory: Optional[Callable[[FtpProfile], object]] = None,
    timeout: float = DEFAULT_FTP_TIMEOUT,
    *,
    token: object = None,
) -> FtpPullResult:
    """Download one preset into ``cache_root/<host>/<preset-key>``.

    A preset with ``target_paths`` pulls only those directories when its root
    is ``/``. Otherwise the whole remote path is mirrored. The local layout
    keeps the remote relative paths so the existing scanners still recognise
    the cache as a card. Unchanged files are copied from the previous cache
    when the skip-list matches. A failed or cancelled pull removes its staging
    directory and leaves the previous cache in place.
    """
    cache_root = Path(cache_root)
    key = sanitize_component(profile.key) or "default"
    host_part = cache_host_component(profile.host)
    final = cache_dir_in(cache_root, key, profile.host)
    staging = cache_root / f".{host_part}.{key}.staging-{os.getpid()}-{uuid4().hex[:8]}"
    factory = client_factory or (lambda preset: RemoteFtpClient(preset, timeout=timeout))

    client = None
    files_out: dict = {}
    try:
        client = factory(profile).connect()
        staging.mkdir(parents=True, exist_ok=True)
        old_root = final if final.is_dir() else None
        manifest = load_manifest(final) if old_root is not None else {}

        target_paths = getattr(profile, "target_paths", ())
        used_target = False
        files = 0
        total = 0

        # If preset specifies targeted save paths and default path was kept, try targeted sync
        if target_paths and (not profile.path or profile.path == "/"):
            for target in target_paths:
                _throw_if_cancelled(token)
                try:
                    entries = client.list_dir(target)
                except Exception:
                    continue
                used_target = True
                clean_parts = [
                    sanitize_component(p)
                    for p in target.replace("\\", "/").split("/")
                    if p
                ]
                clean_parts = [p for p in clean_parts if p is not None]
                if not clean_parts:
                    continue
                sub_staging = staging
                for part in clean_parts:
                    sub_staging = _safe_local_join(sub_staging, part)
                sub_staging.mkdir(parents=True, exist_ok=True)

                sub_files, sub_bytes = _mirror(
                    client,
                    target,
                    sub_staging,
                    old_root=old_root,
                    manifest=manifest,
                    files_out=files_out,
                    token=token,
                    rel="/".join(clean_parts),
                    skip_dir_names=skip_dir_names_for(target),
                    allow_at_depth=allow_dir_names_for(target),
                )
                files += sub_files
                total += sub_bytes

        if not used_target:
            files, total = _mirror(
                client,
                profile.path or "/",
                staging,
                old_root=old_root,
                manifest=manifest,
                files_out=files_out,
                token=token,
            )
    except _FtpPullCancelled:
        _remove_tree(staging)
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        return FtpPullResult(ok=False, preset_key=key, error="已取消")
    except Exception as exc:  # noqa: BLE001 - surfaced as a failed result
        _remove_tree(staging)
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        return FtpPullResult(ok=False, preset_key=key, error=_error_text(exc, profile))
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    # Commit: replace any previous cache with the fully-populated staging tree.
    old: Optional[Path] = None
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            old = cache_root / f".{host_part}.{key}.old-{uuid4().hex[:8]}"
            os.replace(final, old)
        os.replace(staging, final)
    except OSError as exc:
        _remove_tree(staging)
        # Restore the previous cache if the swap failed midway.
        if old is not None and old.exists() and not final.exists():
            try:
                os.replace(old, final)
            except OSError:
                pass
        return FtpPullResult(ok=False, preset_key=key, error=_error_text(exc, profile))
    if old is not None:
        _remove_tree(old)
    save_manifest(final, files_out)
    return FtpPullResult(
        ok=True, preset_key=key, path=final, files=files, total_bytes=total
    )

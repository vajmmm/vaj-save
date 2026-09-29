"""Pull DBI MTP saves into a local, scannable cache directory.

The pull is *atomic* and mirrors :mod:`vajsave.ftp_fetch`: everything lands in a
hidden staging directory first and the device's cache directory is only swapped
in once the whole tree is populated.  Unchanged files are copied from the
previous cache using the shared manifest skip-list.  A failed or cancelled pull
never turns into a half-populated "device", and a previously complete cache is
left untouched.

Only save-bearing MTP storages are mirrored: the ``Saves`` storage in full and
the save subtrees of an SD-card storage (``switch/Checkpoint/saves``, ``JKSV``,
``switch/DBI/saves``).  An ``Installed games`` NSP storage, the Album and the
rest of the SD card are never read.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple
from uuid import uuid4

from .ftp_manifest import load_manifest, save_manifest, should_reuse
from .remote_mtp import (
    MTP_CACHE_DIRNAME,
    SD_SAVE_SUBTREES,
    MtpError,
    MtpNotFound,
    MtpStorage,
    cache_dir_name,
    sanitize_mtp_component,
)

# Bounded recursion so a malformed/looping MTP listing cannot run away.
_MAX_DEPTH = 24


@dataclass
class MtpPullResult:
    """Outcome of one MTP device pull."""

    ok: bool
    device_id: str
    path: Optional[Path] = None
    files: int = 0
    total_bytes: int = 0
    error: str = ""

    def __bool__(self) -> bool:
        return self.ok


@dataclass(frozen=True)
class _MirrorSpec:
    """One storage subtree to mirror into the cache."""

    storage_id: str
    remote_root: str
    local_root: str  # POSIX relative path below the cache root ("" == root)


def mtp_cache_root(library_root) -> Path:
    """The directory holding every MTP device's pulled cache."""
    return Path(library_root) / MTP_CACHE_DIRNAME


def device_cache_dir(library_root, device_id) -> Path:
    """The cache directory for one MTP device (hostile ids cannot escape)."""
    return mtp_cache_root(library_root) / cache_dir_name(device_id)


def plan_for_storages(storages: List[MtpStorage]) -> List[_MirrorSpec]:
    """Which storages/subtrees to pull, in a stable order."""
    plan: List[_MirrorSpec] = []
    for storage in storages:
        if storage.is_saves:
            plan.append(_MirrorSpec(storage.storage_id, "/", ""))
    for storage in storages:
        if storage.is_sd_card:
            for subtree in SD_SAVE_SUBTREES:
                plan.append(_MirrorSpec(storage.storage_id, "/" + subtree, subtree))
    return plan


class _MtpPullCancelled(Exception):
    """Internal: cooperative cancel of an in-flight pull."""


def _throw_if_cancelled(token: object) -> None:
    if token is None:
        return
    raiser = getattr(token, "raise_if_cancelled", None)
    if callable(raiser):
        try:
            raiser()
        except _MtpPullCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - any cancel signal
            raise _MtpPullCancelled("已取消") from exc
    cancelled = getattr(token, "cancelled", None)
    if callable(cancelled) and cancelled():
        raise _MtpPullCancelled("已取消")


def _local_rel_dir(base: Path, rel: str) -> Path:
    """Resolve a POSIX relative path below ``base``, rejecting traversal."""
    current = base
    for part in str(rel or "").replace("\\", "/").split("/"):
        if not part:
            continue
        clean = sanitize_mtp_component(part)
        if clean is None:
            raise MtpError(f"不安全的缓存路径: {part!r}")
        current = _safe_local_join(current, clean)
    return current


def _safe_local_join(base: Path, name: str) -> Path:
    """Resolve ``base / name`` and guarantee it stays under ``base``."""
    clean = sanitize_mtp_component(name)
    if clean is None:
        raise MtpError(f"不安全的缓存路径: {name!r}")
    base_resolved = base.resolve()
    candidate = (base_resolved / clean).resolve()
    if candidate != base_resolved and base_resolved not in candidate.parents:
        raise MtpError(f"缓存路径越界: {name!r}")
    return candidate


def _cached_file(old_root: Path, rel: str) -> Optional[Path]:
    try:
        current = Path(old_root)
        for part in str(rel or "").replace("\\", "/").split("/"):
            if not part:
                continue
            current = _safe_local_join(current, part)
        return current if current.is_file() else None
    except MtpError:
        return None


def _copy_or_download(
    client,
    storage_id: str,
    remote_path: str,
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
    return int(client.download(storage_id, remote_path, target))


def _mirror(
    client,
    storage_id: str,
    remote_dir: str,
    local_dir: Path,
    depth: int = 0,
    *,
    old_root: Optional[Path] = None,
    manifest: Optional[dict] = None,
    files_out: Optional[dict] = None,
    token: object = None,
    rel: str = "",
) -> Tuple[int, int]:
    """Recursively download one remote MTP directory into ``local_dir``."""
    _throw_if_cancelled(token)
    if depth > _MAX_DEPTH:
        return (0, 0)
    files = 0
    total = 0
    recorded_files = manifest if manifest is not None else {}
    collected = files_out if files_out is not None else {}
    for entry in client.list_dir(storage_id, remote_dir):
        _throw_if_cancelled(token)
        clean = sanitize_mtp_component(entry.name)
        if clean is None:
            continue
        target = _safe_local_join(local_dir, clean)
        remote_child = remote_dir.rstrip("/") + "/" + str(entry.name)
        child_rel = "/".join(part for part in (rel, clean) if part)
        if entry.is_dir:
            child_files, child_bytes = _mirror(
                client,
                storage_id,
                remote_child,
                target,
                depth + 1,
                old_root=old_root,
                manifest=recorded_files,
                files_out=collected,
                token=token,
                rel=child_rel,
            )
            files += child_files
            total += child_bytes
        else:
            written = _copy_or_download(
                client,
                storage_id,
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


def _remove_tree(path: Path) -> None:
    try:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _error_text(exc: BaseException) -> str:
    return str(exc) or exc.__class__.__name__


def pull_device(
    client,
    cache_dir,
    *,
    device_id: str = "",
    token: object = None,
    progress: Optional[Callable[[str], None]] = None,
) -> MtpPullResult:
    """Download a Switch MTP device's saves into ``cache_dir`` atomically.

    Unchanged files are copied from the previous cache when the skip-list
    matches.  Returns a result whose ``ok`` is ``False`` on any connection,
    listing, download or cancel failure.  A failed or cancelled pull removes its
    staging directory and leaves the previous cache (and manifest) in place.
    """
    cache_dir = Path(cache_dir)
    staging = cache_dir.parent / (
        f".{cache_dir.name}.staging-{os.getpid()}-{uuid4().hex[:8]}"
    )

    def _emit(message: str) -> None:
        if progress is None:
            return
        try:
            progress(message)
        except Exception:  # noqa: BLE001 - progress must never fail a pull
            pass

    files_out: dict = {}
    files = 0
    total = 0
    try:
        client.connect()
        storages = list(client.list_storages())
        plan = plan_for_storages(storages)
        if not plan:
            raise MtpError("未发现可读取的 Switch 存档存储")
        staging.mkdir(parents=True, exist_ok=True)
        old_root = cache_dir if cache_dir.is_dir() else None
        manifest = load_manifest(cache_dir) if old_root is not None else {}
        for index, spec in enumerate(plan, start=1):
            _throw_if_cancelled(token)
            _emit(f"正在从 DBI MTP 拉取… {index}/{len(plan)}")
            local_dir = _local_rel_dir(staging, spec.local_root)
            try:
                part_files, part_bytes = _mirror(
                    client,
                    spec.storage_id,
                    spec.remote_root,
                    local_dir,
                    old_root=old_root,
                    manifest=manifest,
                    files_out=files_out,
                    token=token,
                    rel=spec.local_root,
                )
            except MtpNotFound:
                # An absent optional SD subtree is not a pull failure.
                continue
            files += part_files
            total += part_bytes
    except _MtpPullCancelled:
        _remove_tree(staging)
        return MtpPullResult(ok=False, device_id=device_id, error="已取消")
    except Exception as exc:  # noqa: BLE001 - surfaced as a failed result
        _remove_tree(staging)
        return MtpPullResult(ok=False, device_id=device_id, error=_error_text(exc))
    finally:
        _close(client)

    # Commit: replace any previous cache with the fully-populated staging tree.
    old: Optional[Path] = None
    try:
        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        if cache_dir.exists():
            old = cache_dir.parent / f".{cache_dir.name}.old-{uuid4().hex[:8]}"
            os.replace(cache_dir, old)
        os.replace(staging, cache_dir)
    except OSError as exc:
        _remove_tree(staging)
        if old is not None and old.exists() and not cache_dir.exists():
            try:
                os.replace(old, cache_dir)
            except OSError:
                pass
        return MtpPullResult(ok=False, device_id=device_id, error=_error_text(exc))
    if old is not None:
        _remove_tree(old)
    save_manifest(cache_dir, files_out)
    return MtpPullResult(
        ok=True, device_id=device_id, path=cache_dir, files=files, total_bytes=total
    )


def _close(client) -> None:
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "MtpPullResult",
    "device_cache_dir",
    "mtp_cache_root",
    "plan_for_storages",
    "pull_device",
]

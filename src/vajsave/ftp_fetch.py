"""Pull handheld saves over FTP into a local, scannable cache directory.

The pull is *atomic*: everything lands in a hidden staging directory first and
the preset's cache directory is only swapped in once the whole tree downloaded
successfully.  A failed or partial pull therefore never turns into a
half-populated "device", and a previously complete cache is left untouched.

The local layout mirrors the remote one, so the existing platform scanners
recognise ``<cache>/<preset>/3ds/Checkpoint/saves/...`` as a normal 3DS/Switch
Checkpoint card.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple
from uuid import uuid4

from .remote_ftp import (
    DEFAULT_FTP_TIMEOUT,
    FtpProfile,
    RemoteFtpClient,
    RemoteFtpError,
    redact,
    sanitize_component,
)

FTP_CACHE_DIRNAME = "ftp-cache"

# Bounded recursion so a malformed/looping server listing cannot run away.
_MAX_DEPTH = 16


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


def cache_dir_for(library_root, preset_key: str) -> Path:
    """The cache directory for one preset (hostile keys cannot escape)."""
    key = sanitize_component(preset_key) or "default"
    return ftp_cache_root(library_root) / key


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


def _mirror(client, remote_dir: str, local_dir: Path, depth: int = 0) -> Tuple[int, int]:
    """Recursively download ``remote_dir`` into ``local_dir``.

    Unsafe entry names are skipped rather than followed, and every local target
    is re-validated to stay inside the staging tree.
    """
    if depth > _MAX_DEPTH:
        return (0, 0)
    files = 0
    total = 0
    for entry in client.list_dir(remote_dir):
        clean = sanitize_component(entry.name)
        if clean is None:
            continue
        target = _safe_local_join(local_dir, clean)
        remote_child = remote_dir.rstrip("/") + "/" + clean
        if entry.is_dir:
            child_files, child_bytes = _mirror(client, remote_child, target, depth + 1)
            files += child_files
            total += child_bytes
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            total += int(client.download(remote_child, target))
            files += 1
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
) -> FtpPullResult:
    """Download one preset's remote tree into ``cache_root/<preset-key>``.

    Returns a result whose ``ok`` is ``False`` on any connection, listing or
    download failure.  A failed pull removes its staging directory and leaves
    the previous cache (if any) in place.
    """
    cache_root = Path(cache_root)
    key = sanitize_component(profile.key) or "default"
    final = cache_root / key
    staging = cache_root / f".{key}.staging-{os.getpid()}-{uuid4().hex[:8]}"
    factory = client_factory or (lambda preset: RemoteFtpClient(preset, timeout=timeout))

    client = None
    try:
        client = factory(profile).connect()
        staging.mkdir(parents=True, exist_ok=True)
        files, total = _mirror(client, profile.path or "/", staging)
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

    # Commit: replace any previous cache with the fully-downloaded staging tree.
    old: Optional[Path] = None
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
        if final.exists():
            old = cache_root / f".{key}.old-{uuid4().hex[:8]}"
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
    return FtpPullResult(
        ok=True, preset_key=key, path=final, files=files, total_bytes=total
    )

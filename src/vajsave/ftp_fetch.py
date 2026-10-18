"""Pull handheld saves over FTP into a local, scannable cache directory.

Plaintext export roots are published as soon as they finish, so the gallery
can show them while a larger encrypted tree is still downloading. A failure
before that publish removes the staging directory and leaves the previous
cache in place. Unchanged files are copied from the previous cache using the
``.ftp-manifest.json`` skip-list.

The local layout mirrors the remote one, so the existing platform scanners
recognise ``<cache>/<host>/<preset>/3ds/Checkpoint/saves/...`` as a normal card.
A preset with ``target_paths`` pulls only those directories.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple
from uuid import uuid4

from .ftp_manifest import load_manifest, save_manifest, should_reuse
from .remote_ftp import (
    DEFAULT_FTP_TIMEOUT,
    FtpProfile,
    RemoteFtpClient,
    RemoteFtpError,
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
    warning: str = ""

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


def _short_remote(path: str) -> str:
    text = str(path or "").replace("\\", "/").strip("/") or "/"
    if len(text) > 56:
        return "…" + text[-55:]
    return text


def _emit_progress(progress, message: str, files: int, total: int = 0) -> None:
    if progress is None:
        return
    try:
        progress(message, files, total)
    except Exception:  # noqa: BLE001 - progress must not fail a pull
        pass


def _keep_n3ds_file(rel: str) -> bool:
    """Encrypted-SD files the 3DS scanner can actually read."""
    parts = [part.casefold() for part in str(rel or "").replace("\\", "/").split("/") if part]
    if "extdata" in parts:
        return True
    return "data" in parts and bool(parts) and parts[-1].endswith(".sav")


def _is_encrypted_sd_root(target: str) -> bool:
    return str(target or "").replace("\\", "/").strip("/").lower() == "nintendo 3ds"


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
    optional: bool = False,
    progress=None,
    counter: Optional[dict] = None,
    keep_file: Optional[Callable[[str], bool]] = None,
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
    seen = counter if counter is not None else {"files": 0}
    _emit_progress(progress, f"正在读取 {_short_remote(remote_dir)}", int(seen.get("files", 0)))
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
    # An empty game folder is still a Checkpoint entry. Keep the directory.
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
                progress=progress,
                counter=seen,
                keep_file=keep_file,
            )
            files += child_files
            total += child_bytes
        else:
            if keep_file is not None and not keep_file(child_rel):
                continue
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
            seen["files"] = int(seen.get("files", 0)) + 1
            _emit_progress(
                progress,
                f"正在拉取 {_short_remote(child_rel)} · {seen['files']} 个文件",
                int(seen["files"]),
            )
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


def _swap_cache(staging: Path, final: Path, cache_root: Path, host_part: str, key: str) -> Optional[Path]:
    """Replace ``final`` with ``staging``. Return the displaced previous cache."""
    cache_root.mkdir(parents=True, exist_ok=True)
    final.parent.mkdir(parents=True, exist_ok=True)
    displaced: Optional[Path] = None
    try:
        if final.exists():
            displaced = cache_root / f".{host_part}.{key}.old-{uuid4().hex[:8]}"
            os.replace(final, displaced)
        os.replace(staging, final)
    except OSError:
        _remove_tree(staging)
        if displaced is not None and displaced.exists() and not final.exists():
            try:
                os.replace(displaced, final)
            except OSError:
                pass
        raise
    return displaced


def pull_preset(
    profile: FtpProfile,
    cache_root,
    client_factory: Optional[Callable[[FtpProfile], object]] = None,
    timeout: float = DEFAULT_FTP_TIMEOUT,
    *,
    token: object = None,
    progress: Optional[Callable[[str, int, int], None]] = None,
    on_ready: Optional[Callable[[Path], None]] = None,
) -> FtpPullResult:
    """Download one preset into ``cache_root/<host>/<preset-key>``.

    A preset with ``target_paths`` pulls only those directories when its root
    is ``/``. Otherwise the whole remote path is mirrored. Plaintext export
    roots are published before the encrypted ``Nintendo 3DS`` tree, so the
    gallery can show Checkpoint saves while that larger tree is still coming
    down. A failure before that publish leaves the previous cache untouched.
    """
    cache_root = Path(cache_root)
    key = sanitize_component(profile.key) or "default"
    host_part = cache_host_component(profile.host)
    final = cache_dir_in(cache_root, key, profile.host)
    staging = cache_root / f".{host_part}.{key}.staging-{os.getpid()}-{uuid4().hex[:8]}"
    factory = client_factory or (lambda preset: RemoteFtpClient(preset, timeout=timeout))

    client = None
    files_out: dict = {}
    counter = {"files": 0}
    files = 0
    total = 0
    published = False
    kept_old: Optional[Path] = None

    def _published_result(warning: str) -> FtpPullResult:
        save_manifest(final, files_out)
        if kept_old is not None:
            _remove_tree(kept_old)
        return FtpPullResult(
            ok=True,
            preset_key=key,
            path=final,
            files=files,
            total_bytes=total,
            warning=warning,
        )

    try:
        _emit_progress(progress, f"正在连接 {profile.host}:{profile.port}", 0)
        client = factory(profile).connect()
        staging.mkdir(parents=True, exist_ok=True)
        old_root = final if final.is_dir() else None
        manifest = load_manifest(final) if old_root is not None else {}

        def _pull_into(dest: Path, targets, reuse_root: Optional[Path]) -> Tuple[bool, int, int]:
            used = False
            got_files = 0
            got_bytes = 0
            for target in targets:
                _throw_if_cancelled(token)
                try:
                    client.list_dir(target)
                except Exception:
                    continue
                clean_parts = [
                    sanitize_component(part)
                    for part in str(target).replace("\\", "/").split("/")
                    if part
                ]
                clean_parts = [part for part in clean_parts if part is not None]
                if not clean_parts:
                    continue
                used = True
                sub = dest
                for part in clean_parts:
                    sub = _safe_local_join(sub, part)
                sub.mkdir(parents=True, exist_ok=True)
                encrypted = _is_encrypted_sd_root(target)
                sub_files, sub_bytes = _mirror(
                    client,
                    target,
                    sub,
                    old_root=reuse_root,
                    manifest=manifest,
                    files_out=files_out,
                    token=token,
                    rel="/".join(clean_parts),
                    skip_dir_names=skip_dir_names_for(target),
                    progress=progress,
                    counter=counter,
                    keep_file=_keep_n3ds_file if encrypted else None,
                )
                got_files += sub_files
                got_bytes += sub_bytes
            return used, got_files, got_bytes

        target_paths = tuple(getattr(profile, "target_paths", ()) or ())
        targeted = bool(target_paths) and (not profile.path or profile.path == "/")
        if not targeted:
            files, total = _mirror(
                client,
                profile.path or "/",
                staging,
                old_root=old_root,
                manifest=manifest,
                files_out=files_out,
                token=token,
                progress=progress,
                counter=counter,
            )
        else:
            fast = [target for target in target_paths if not _is_encrypted_sd_root(target)]
            slow = [target for target in target_paths if _is_encrypted_sd_root(target)]
            used_fast, files, total = _pull_into(staging, fast, old_root)
            if used_fast and slow:
                kept_old = _swap_cache(staging, final, cache_root, host_part, key)
                published = True
                _emit_progress(progress, f"存档目录已可读 · {files} 个文件，继续拉取加密 SD", files)
                if on_ready is not None:
                    try:
                        on_ready(final)
                    except Exception:  # noqa: BLE001 - the UI refresh must not abort the pull
                        pass
                _, slow_files, slow_bytes = _pull_into(final, slow, kept_old)
                files += slow_files
                total += slow_bytes
            elif slow:
                used_slow, slow_files, slow_bytes = _pull_into(staging, slow, old_root)
                files += slow_files
                total += slow_bytes
                if not used_fast and not used_slow:
                    files, total = _mirror(
                        client,
                        profile.path or "/",
                        staging,
                        old_root=old_root,
                        manifest=manifest,
                        files_out=files_out,
                        token=token,
                        progress=progress,
                        counter=counter,
                    )
            elif not used_fast:
                files, total = _mirror(
                    client,
                    profile.path or "/",
                    staging,
                    old_root=old_root,
                    manifest=manifest,
                    files_out=files_out,
                    token=token,
                    progress=progress,
                    counter=counter,
                )
    except _FtpPullCancelled:
        if published:
            return _published_result("已取消后续拉取")
        _remove_tree(staging)
        return FtpPullResult(ok=False, preset_key=key, error="已取消")
    except Exception as exc:  # noqa: BLE001 - surfaced as a failed result
        if published:
            return _published_result(_error_text(exc, profile))
        _remove_tree(staging)
        return FtpPullResult(ok=False, preset_key=key, error=_error_text(exc, profile))
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    if not published:
        try:
            kept_old = _swap_cache(staging, final, cache_root, host_part, key)
        except OSError as exc:
            return FtpPullResult(ok=False, preset_key=key, error=_error_text(exc, profile))
    save_manifest(final, files_out)
    if kept_old is not None:
        _remove_tree(kept_old)
    return FtpPullResult(
        ok=True, preset_key=key, path=final, files=files, total_bytes=total
    )

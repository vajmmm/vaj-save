"""Pure filesystem hashing and tree-copying utilities with stat caching."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HASH_CHUNK = 1024 * 1024
_HASH_CACHE_MAX = 2048
_hash_cache: Dict[Tuple[Any, ...], str] = {}
_hash_cache_lock = threading.Lock()


def _update_from_file(digest: Any, path: Path) -> None:
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)


def _file_fingerprint(path: Path) -> Tuple[Any, ...]:
    st = path.stat()
    return ("file", str(path.resolve()), st.st_mtime_ns, st.st_size)


def _dir_file_list(root: Path) -> List[Tuple[Path, str, int, int]]:
    files: List[Tuple[Path, str, int, int]] = []
    root_str = str(root)
    for dirpath, dirnames, filenames in os.walk(root_str, followlinks=False):
        dirnames[:] = [
            d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
        ]
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            if os.path.islink(fpath):
                continue
            try:
                st = os.stat(fpath)
                if not stat.S_ISREG(st.st_mode):
                    continue
            except OSError:
                continue
            child = Path(fpath)
            rel = child.relative_to(root).as_posix()
            files.append((child, rel, st.st_mtime_ns, st.st_size))
    files.sort(key=lambda item: item[1])
    return files


def _cache_get(key: Tuple[Any, ...]) -> Optional[str]:
    with _hash_cache_lock:
        return _hash_cache.get(key)


def _cache_put(key: Tuple[Any, ...], value: str) -> None:
    with _hash_cache_lock:
        if len(_hash_cache) >= _HASH_CACHE_MAX:
            _hash_cache.clear()
        _hash_cache[key] = value


def hash_tree(path: Path) -> str:
    """Stable sha256 of a file or directory (skips symlinks). Streamed; stat-cacheable."""
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(f"存档路径不存在: {root}")
    if root.is_symlink():
        raise ValueError(f"跳过符号链接: {root}")
    if root.is_file():
        cache_key = _file_fingerprint(root)
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        digest = hashlib.sha256()
        digest.update(b"file\0")
        _update_from_file(digest, root)
        hexdigest = digest.hexdigest()
        _cache_put(cache_key, hexdigest)
        return hexdigest

    listed = _dir_file_list(root)
    cache_key = ("dir", str(root.resolve()), tuple((rel, mtime, size) for _p, rel, mtime, size in listed))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    for child, rel, _mtime, size in listed:
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        _update_from_file(digest, child)
    hexdigest = digest.hexdigest()
    _cache_put(cache_key, hexdigest)
    return hexdigest


def copy_save_tree(source: Path, dest_dir: Path) -> Path:
    """Copy a file or directory into dest_dir, skipping symlinks."""
    source = Path(source)
    dest_dir = Path(dest_dir)
    if not source.exists():
        raise FileNotFoundError(f"存档路径不存在: {source}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / source.name
    if source.is_symlink():
        raise ValueError(f"跳过符号链接: {source}")
    if source.is_file():
        shutil.copyfile(source, target, follow_symlinks=False)
        return target
    if not source.is_dir():
        raise ValueError(f"无法复制: {source}")
    _copy_dir(source, target)
    return target


def _copy_dir(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir()):
        if child.is_symlink():
            continue
        next_dest = dest / child.name
        if child.is_dir():
            _copy_dir(child, next_dest)
        elif child.is_file():
            shutil.copyfile(child, next_dest, follow_symlinks=False)

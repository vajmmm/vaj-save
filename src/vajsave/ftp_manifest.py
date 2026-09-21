"""Skip-list for incremental FTP pulls.

A successful pull writes ``.ftp-manifest.json`` next to the cached files.
The next pull copies an unchanged file from the previous cache instead of
downloading it when LIST size (and modified, when present) match.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .persistence import atomic_write_json
from .remote_ftp import is_safe_relative_path

MANIFEST_NAME = ".ftp-manifest.json"
MANIFEST_FORMAT = "vaj-save-ftp-manifest"
MANIFEST_FORMAT_VERSION = 1


def _record(value: object) -> Optional[dict]:
    if not isinstance(value, dict):
        return None
    try:
        size = int(value.get("size", 0) or 0)
    except (TypeError, ValueError):
        return None
    modified = value.get("modified", "")
    return {"size": size, "modified": "" if modified is None else str(modified)}


def load_manifest(cache_dir: Path) -> dict[str, dict]:
    """Return the files table from ``cache_dir``, or ``{}`` if missing/corrupt."""
    path = Path(cache_dir) / MANIFEST_NAME
    try:
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(raw, dict) or raw.get("format") != MANIFEST_FORMAT:
        return {}
    try:
        version = int(raw.get("format_version", 0) or 0)
    except (TypeError, ValueError):
        return {}
    if version != MANIFEST_FORMAT_VERSION:
        return {}
    files = raw.get("files")
    if not isinstance(files, dict):
        return {}
    out: dict[str, dict] = {}
    for key, value in files.items():
        rel = str(key or "").replace("\\", "/")
        if not is_safe_relative_path(rel):
            continue
        record = _record(value)
        if record is None:
            continue
        out[rel] = record
    return out


def save_manifest(cache_dir: Path, files: dict[str, dict]) -> bool:
    """Atomically write the skip-list; ``False`` on any serialisation/OS error."""
    payload_files: dict[str, dict] = {}
    for key, value in (files or {}).items():
        rel = str(key or "").replace("\\", "/")
        if not is_safe_relative_path(rel):
            continue
        record = _record(value)
        if record is None:
            continue
        payload_files[rel] = record
    return atomic_write_json(
        Path(cache_dir) / MANIFEST_NAME,
        {
            "format": MANIFEST_FORMAT,
            "format_version": MANIFEST_FORMAT_VERSION,
            "files": payload_files,
        },
    )


def should_reuse(
    entry_size: int, entry_modified: str, recorded: dict | None
) -> bool:
    """True when LIST size matches and mtime agrees (or LIST has no mtime)."""
    if not isinstance(recorded, dict):
        return False
    try:
        rec_size = int(recorded.get("size"))
        listing_size = int(entry_size)
    except (TypeError, ValueError):
        return False
    if rec_size != listing_size:
        return False
    rec_mod = "" if recorded.get("modified") is None else str(recorded.get("modified") or "")
    listing_mod = "" if entry_modified is None else str(entry_modified or "")
    if listing_mod == rec_mod:
        return True
    # LIST has no mtime: size-only match is enough (user-chosen heuristic).
    return listing_mod == ""

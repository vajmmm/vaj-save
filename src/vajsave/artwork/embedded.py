"""Embedded icon and cover discovery inside save folders."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from .thumbnails import _as_path, _within_size_limit

# Supported cover/thumbnail extensions, in lookup order.
IMAGE_EXTENSIONS = ("png", "jpg", "jpeg", "webp", "bmp", "gif")

# File-name stems that consoles/backup tools drop inside a save folder.
_EMBEDDED_ICON_STEMS = (
    "icon0",
    "icon",
    "pic1",
    "thumb",
    "preview",
    "folder",
    "cover",
    "banner",
    "boxart",
)

EMBEDDED_ICON_NAMES = tuple(
    f"{stem}.{ext}" for stem in _EMBEDDED_ICON_STEMS for ext in IMAGE_EXTENSIONS
)

EMBEDDED_COVER_NAMES = EMBEDDED_ICON_NAMES

# Substrings that promote a generically-scanned file to the front of the list
_GENERIC_NAME_HINTS = ("cover", "icon", "box")

# Sub-directories that hold an official icon, in probe order.
_CANONICAL_SUBDIRS = ("sce_sys", "media", "icon")


def _sort_key(path: Path) -> tuple:
    name = path.name.lower()
    hinted = 0 if any(hint in name for hint in _GENERIC_NAME_HINTS) else 1
    return (hinted, name)


def _dir_entries(path: Path) -> List[Path]:
    try:
        if not path.is_dir():
            return []
        return list(path.iterdir())
    except OSError:
        return []


def _match_icon(directory: Path) -> Optional[Path]:
    entries = _dir_entries(directory)
    if not entries:
        return None
    by_name = {}
    for entry in entries:
        if _within_size_limit(entry):
            by_name.setdefault(entry.name.lower(), entry)
    for name in EMBEDDED_ICON_NAMES:
        found = by_name.get(name)
        if found is not None:
            return found
    return None


def _generic_icon_candidates(directory: Path) -> List[Path]:
    candidates: List[Path] = []
    for entry in _dir_entries(directory):
        if entry.suffix.lower().lstrip(".") not in IMAGE_EXTENSIONS:
            continue
        if not _within_size_limit(entry):
            continue
        candidates.append(entry)
    candidates.sort(key=_sort_key)
    return candidates


def _ordered_subdirs(directory: Path) -> List[Path]:
    subs: List[Path] = []
    for entry in _dir_entries(directory):
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                subs.append(entry)
        except OSError:
            continue
    subs.sort(key=lambda item: item.name.lower())
    rank = {name: index for index, name in enumerate(_CANONICAL_SUBDIRS)}
    subs.sort(key=lambda item: rank.get(item.name.lower(), len(rank)))
    return subs


def _sibling_cover(save_file: Path) -> Optional[Path]:
    parent = save_file.parent
    entries = _dir_entries(parent)
    if not entries:
        return None
    by_name = {}
    for entry in entries:
        if _within_size_limit(entry):
            by_name.setdefault(entry.name.lower(), entry)
    stem = save_file.stem.lower()
    for ext in IMAGE_EXTENSIONS:
        found = by_name.get(f"{stem}.{ext}")
        if found is not None:
            return found
    for name in EMBEDDED_ICON_NAMES:
        found = by_name.get(name)
        if found is not None:
            return found
    return None


def find_embedded_cover(save_path: Any, *, max_depth: int = 1) -> Optional[Path]:
    """Find an icon embedded in a save folder (or next to a raw save file).

    Probes in priority order:
    1. well-known icon names inside the save folder itself;
    2. well-known names inside subdirectories (sce_sys -> media -> icon);
    3. deterministic generic scan of image files.
    """
    path = _as_path(save_path)
    if path is None:
        return None
    try:
        if path.is_file():
            return _sibling_cover(path)
        if not path.is_dir():
            return None
    except OSError:
        return None

    subs = _ordered_subdirs(path) if max_depth >= 1 else []

    found = _match_icon(path)
    if found is not None:
        return found
    for sub in subs:
        found = _match_icon(sub)
        if found is not None:
            return found

    candidates = _generic_icon_candidates(path)
    if candidates:
        return candidates[0]
    for sub in subs:
        candidates = _generic_icon_candidates(sub)
        if candidates:
            return candidates[0]
    return None

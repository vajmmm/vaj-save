"""Path computation and lookup for user and downloaded covers."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, List, Optional

from ..library_models import sanitize_name
from .cache import DOWNLOADED_COVER_DIR, identity_hash
from .embedded import IMAGE_EXTENSIONS, _dir_entries
from .thumbnails import _as_path, _within_size_limit


def _safe_component(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    cleaned = sanitize_name(text, "")
    cleaned = cleaned.strip(" .")
    return cleaned or None


def _cover_stems(entry: Any) -> List[str]:
    stems: List[str] = []
    for raw in (getattr(entry, "title_id", None), getattr(entry, "display_name", None)):
        stem = _safe_component(raw)
        if stem and stem.lower() not in {s.lower() for s in stems}:
            stems.append(stem)
    return stems


def user_cover_path(entry: Any, library_root: Any) -> Optional[Path]:
    """Return the user-supplied cover for ``entry``, or ``None``."""
    try:
        root = _as_path(library_root)
        if root is None or entry is None:
            return None
        platform = _safe_component(getattr(entry, "platform", None)) or "unknown"
        stems = _cover_stems(entry)
        if not stems:
            return None
        directory = root / "covers" / platform
        entries = _dir_entries(directory)
        if not entries:
            return None
        by_name = {}
        for candidate in entries:
            if _within_size_limit(candidate):
                by_name.setdefault(candidate.name.lower(), candidate)
        for stem in stems:
            for ext in IMAGE_EXTENSIONS:
                found = by_name.get(f"{stem}.{ext}".lower())
                if found is not None:
                    return found
        return None
    except Exception:  # noqa: BLE001
        return None


def downloaded_cover_path(
    library_root: Any, platform: Any, identity_key: Any
) -> Optional[Path]:
    """Return the cached downloaded cover for an identity, or ``None``."""
    try:
        root = _as_path(library_root)
        if root is None or not identity_key:
            return None
        from .cache import CoverCache, is_portrait_image_file

        candidate = CoverCache.path_in(
            root / DOWNLOADED_COVER_DIR, platform, identity_key
        )
        return (
            candidate
            if candidate is not None
            and _within_size_limit(candidate)
            and is_portrait_image_file(candidate)
            else None
        )
    except Exception:  # noqa: BLE001
        return None


def resolve_cover(
    entry: Any,
    library_root: Any,
    *,
    identity_key: Any = None,
    platform: Any = None,
) -> Optional[Path]:
    """Resolve a portrait/square cover for ``entry``: user > downloaded > embedded."""
    try:
        if entry is None:
            return None
        from .cache import CoverCache
        from .service import resolve_artwork

        root = _as_path(library_root)
        cache = None
        if root is not None and identity_key:
            cache = CoverCache(root / DOWNLOADED_COVER_DIR)
        resolution = resolve_artwork(
            entry,
            library_root,
            cache=cache,
            identity_key=identity_key,
            platform=platform,
        )
        return _as_path(resolution.path) if resolution.path else None
    except Exception:  # noqa: BLE001
        return None

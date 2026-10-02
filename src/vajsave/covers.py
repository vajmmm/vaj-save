"""Cover artwork discovery and thumbnail loading for save tiles.

Backward compatibility facade for :mod:`vajsave.artwork`.
All underlying implementations have been modularized under :mod:`vajsave.artwork`.
"""

from __future__ import annotations

from .artwork import (
    DEFAULT_THUMBNAIL_RADIUS,
    DOWNLOADED_COVER_DIR,
    EMBEDDED_COVER_NAMES,
    EMBEDDED_ICON_NAMES,
    IMAGE_EXTENSIONS,
    MAX_COVER_BYTES,
    MAX_COVER_RESIZE_DIMENSION,
    downloaded_cover_path,
    find_embedded_cover,
    identity_hash,
    load_thumbnail,
    resolve_cover,
    user_cover_path,
)
from .artwork.embedded import (
    _CANONICAL_SUBDIRS,
    _EMBEDDED_ICON_STEMS,
    _GENERIC_NAME_HINTS,
    _dir_entries,
    _generic_icon_candidates,
    _match_icon,
    _ordered_subdirs,
    _sibling_cover,
    _sort_key,
)
from .artwork.paths import _cover_stems, _safe_component
from .artwork.thumbnails import (
    _as_path,
    _cover_crop_box,
    _cover_fit,
    _round_corners,
    _within_size_limit,
)

__all__ = [
    "IMAGE_EXTENSIONS",
    "EMBEDDED_ICON_NAMES",
    "EMBEDDED_COVER_NAMES",
    "MAX_COVER_BYTES",
    "MAX_COVER_RESIZE_DIMENSION",
    "DEFAULT_THUMBNAIL_RADIUS",
    "DOWNLOADED_COVER_DIR",
    "identity_hash",
    "find_embedded_cover",
    "user_cover_path",
    "downloaded_cover_path",
    "resolve_cover",
    "load_thumbnail",
]

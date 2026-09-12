"""Cover artwork discovery and thumbnail loading for save tiles.

The module is intentionally self-contained and side-effect free:

* it is **read-only** — it only stats/reads existing files and never writes,
  creates, or mutates anything;
* it **never raises** — every public helper degrades to ``None``;
* it **does not import tkinter** — thumbnails come back as Pillow ``RGBA``
  images, and the UI layer wraps them in ``ImageTk.PhotoImage`` itself.

Cover resolution follows a four layer priority:

1. the user cover directory ``<library_root>/covers/<platform>/<name>.<ext>``
   (see :func:`user_cover_path`) — an explicit file the user dropped in;
2. a downloaded cover ``<library_root>/covers/<platform>/<identity-hash>.png``
   (see :func:`downloaded_cover_path`) — the identity-hash cover cache;
3. ``SaveEntry.cover_path`` — an icon found inside the save folder during the
   device scan (see :func:`find_embedded_cover`);
4. ``None`` — the UI then paints the pastel fallback face.

This module stays pure-local: it never touches the network.  Downloading lives
in :mod:`vajsave.artwork`, which writes into the same ``covers/`` tree.

A hard file-size ceiling (:data:`MAX_COVER_BYTES`) guards every lookup: a tiny
PNG can still declare an enormous canvas (e.g. ``200000x1``), so oversized files
are treated as "no cover" before any decode work happens. The thumbnail resize
itself is likewise bounded (:data:`MAX_COVER_RESIZE_DIMENSION`): the source is
cropped to the target aspect ratio *first* and resized straight to the requested
size, so no aspect-inflated intermediate buffer is ever produced.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, List, Optional

from .library import sanitize_name

# Supported cover/thumbnail extensions, in lookup order.
IMAGE_EXTENSIONS = ("png", "jpg", "jpeg", "webp", "bmp", "gif")

# File-name stems that consoles/backup tools drop inside a save folder. Matched
# case-insensitively, in this priority order, so ``ICON0.PNG`` (PSP),
# ``sce_sys/icon0.png`` (Vita), ``pic1.png`` (PS3/PSP background) and the
# homebrew/backup-tool staples (``thumb``/``preview``/``folder``/``cover``) all
# resolve. Expanding the stems over :data:`IMAGE_EXTENSIONS` keeps the ordering
# explicit: e.g. every ``icon0.*`` before every ``icon.*``.
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

# Back-compat alias: the design spec refers to this list as
# ``EMBEDDED_COVER_NAMES``; ``EMBEDDED_ICON_NAMES`` is the historical code name.
# Both expose the same priority-ordered tuple.
EMBEDDED_COVER_NAMES = EMBEDDED_ICON_NAMES

# Hard cap on a single cover/icon file we are willing to read or decode. Cheap
# pre-filter (one ``stat``) that rejects the "tiny file, huge canvas" class of
# resource-exhaustion inputs before Pillow touches them.
MAX_COVER_BYTES = 8 * 1024 * 1024

# Upper bound on any single resize target used while fitting a cover. The crop
# is taken before the resize, so a tile-sized request (104x66) never asks Pillow
# for an oversized intermediate; requests above this ceiling are refused outright
# so the bound is explicit and regression-testable without memory probes.
MAX_COVER_RESIZE_DIMENSION = 4096

# Default corner radius for the rounded thumbnail produced by ``load_thumbnail``.
DEFAULT_THUMBNAIL_RADIUS = 12

# Substrings that promote a generically-scanned file to the front of the list,
# so ``cover_art.png`` beats ``aaa.png`` even though it sorts later.
_GENERIC_NAME_HINTS = ("cover", "icon", "box")

# Sub-directories that hold an official icon, in probe order. ``sce_sys`` is the
# Vita location, so it has to beat the plain alphabetical order that would
# otherwise reach ``icon/`` first.
_CANONICAL_SUBDIRS = ("sce_sys", "media", "icon")

# Directory (under the library root) holding every downloaded/user cover, plus
# the manifest that records the downloaded ones.
DOWNLOADED_COVER_DIR = "covers"


def identity_hash(identity_key: Any) -> str:
    """Stable on-disk name for a game identity: ``sha1(identity_key)`` hex.

    Using a hash instead of the raw ``platform:sha1:...`` key keeps the file
    name short, filesystem-safe and independent of the title.
    """
    return hashlib.sha1(str(identity_key or "").encode("utf-8")).hexdigest()


def _as_path(value: Any) -> Optional[Path]:
    if value is None:
        return None
    try:
        return value if isinstance(value, Path) else Path(value)
    except (TypeError, ValueError, OSError):
        return None


def _within_size_limit(path: Path) -> bool:
    """True when ``path`` is a regular file no larger than ``MAX_COVER_BYTES``.

    A missing/unreadable path or a directory returns ``False`` so callers can
    treat "unusable" and "too large" identically (never raise).
    """
    try:
        if not path.is_file():
            return False
        return path.stat().st_size <= MAX_COVER_BYTES
    except (OSError, ValueError):
        return False


def _sort_key(path: Path) -> tuple:
    """Deterministic generic-scan order: name hints first, then lowercase name."""
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
    """Return the first known icon inside ``directory`` (case-insensitive)."""
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
    """Image files in ``directory`` eligible as a fallback icon, sorted."""
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
    """Real (non-symlink) child directories, in icon-probe order.

    Canonical cover locations (``sce_sys`` -> ``media`` -> ``icon``) come first so
    the official artwork wins regardless of filename sort order; every remaining
    sub-directory follows in lowercase-name order, keeping the result deterministic.
    """
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
    """Look for ``<stem>.<ext>`` or a known icon next to a raw ``.sav`` file."""
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

    Probes run in this order, so *official* artwork always outranks incidental
    images (a stray screenshot in the save root can never shadow the icon a
    console actually ships):

    1. well-known icon names inside the save folder itself (``ICON0.PNG`` ...);
    2. the same well-known names inside its sub-directories, probing ``sce_sys``
       -> ``media`` -> ``icon`` first (Vita's ``sce_sys/icon0.png``);
    3. a deterministic generic scan of image files in the save folder, then in
       its sub-directories.

    Fixed-name probes therefore outrank the generic scan at every depth. Returns
    the first match, or ``None``. Never raises, never recurses beyond
    ``max_depth`` levels, and skips oversized files.
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

    # 1 + 2: well-known icon names, shallow depth first.
    found = _match_icon(path)
    if found is not None:
        return found
    for sub in subs:
        found = _match_icon(sub)
        if found is not None:
            return found

    # 3: deterministic generic image scan, shallow depth first.
    candidates = _generic_icon_candidates(path)
    if candidates:
        return candidates[0]
    for sub in subs:
        candidates = _generic_icon_candidates(sub)
        if candidates:
            return candidates[0]
    return None


def _safe_component(value: Any) -> Optional[str]:
    """Sanitize a single path component, dropping empty/unusable input.

    Critically this strips ``:`` (and the other Windows-illegal characters) so a
    ``game_key`` style ``platform:title:slot`` string can never become a
    filename on Windows.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    cleaned = sanitize_name(text, "")
    cleaned = cleaned.strip(" .")
    return cleaned or None


def _cover_stems(entry: Any) -> List[str]:
    """Candidate file-name stems for an entry, most specific first."""
    stems: List[str] = []
    for raw in (getattr(entry, "title_id", None), getattr(entry, "display_name", None)):
        stem = _safe_component(raw)
        if stem and stem.lower() not in {s.lower() for s in stems}:
            stems.append(stem)
    return stems


def user_cover_path(entry: Any, library_root: Any) -> Optional[Path]:
    """Return the user-supplied cover for ``entry``, or ``None``.

    Looks under ``<library_root>/covers/<platform>/`` for any
    ``<name>.<ext>`` where ``<name>`` is the sanitized title id or display name.
    Only existing, size-bounded files are returned.
    """
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
    except Exception:  # noqa: BLE001 - a cover lookup must never break the UI
        return None


def downloaded_cover_path(
    library_root: Any, platform: Any, identity_key: Any
) -> Optional[Path]:
    """Return the cached downloaded cover for an identity, or ``None``.

    Thin compatibility wrapper: the path layout is owned by
    :meth:`vajsave.artwork.cache.CoverCache.path_in`, so this helper can never
    drift from the directory the artwork layer actually writes into.  Only an
    existing, size-bounded file is reported as a hit.
    """
    try:
        root = _as_path(library_root)
        if root is None or not identity_key:
            return None
        from .artwork.cache import CoverCache

        candidate = CoverCache.path_in(
            root / DOWNLOADED_COVER_DIR, platform, identity_key
        )
        return candidate if candidate is not None and _within_size_limit(candidate) else None
    except Exception:  # noqa: BLE001 - a cover lookup must never break the UI
        return None


def resolve_cover(
    entry: Any,
    library_root: Any,
    *,
    identity_key: Any = None,
    platform: Any = None,
) -> Optional[Path]:
    """Resolve the cover for ``entry``: user > downloaded > embedded > ``None``.

    Thin compatibility wrapper over :func:`vajsave.artwork.resolve_artwork`,
    which owns the single real implementation of the fallback order.  The
    manifest-backed :class:`~vajsave.artwork.cache.CoverCache` is used for the
    downloaded layer, so this stays pure-local (never touches the network) and
    never raises.
    """
    try:
        if entry is None:
            return None
        from .artwork.cache import CoverCache
        from .artwork.service import resolve_artwork

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
    except Exception:  # noqa: BLE001 - a cover lookup must never break the UI
        return None


def _cover_crop_box(src_w: int, src_h: int, target_w: int, target_h: int) -> tuple:
    """Largest centred source rect whose aspect matches ``target_w:target_h``.

    Returns integer ``(left, top, right, bottom)`` pixel bounds. Cropping before
    the resize is what keeps the downstream resize target bounded: a very wide
    source (200000x1) yields a thin crop (about 2x1) instead of an inflated
    scaled buffer.
    """
    target_ratio = target_w / target_h
    src_ratio = src_w / src_h
    if src_ratio > target_ratio:
        crop_w = max(1, min(src_w, int(round(src_h * target_ratio))))
        crop_h = src_h
    else:
        crop_h = max(1, min(src_h, int(round(src_w / target_ratio))))
        crop_w = src_w
    left = (src_w - crop_w) // 2
    top = (src_h - crop_h) // 2
    return (left, top, left + crop_w, top + crop_h)


def _cover_fit(image: Any, width: int, height: int) -> Any:
    """Scale+crop ``image`` to exactly ``width``x``height`` keeping its ratio."""
    from PIL import Image

    rgba = image.convert("RGBA")
    src_w, src_h = rgba.size
    if src_w <= 0 or src_h <= 0:
        return Image.new("RGBA", (width, height), (0, 0, 0, 0))
    # Crop to the target aspect first, then resize straight to the requested
    # thumbnail. The resize target is therefore the tile geometry, never a
    # source-ratio-inflated intermediate (which could be hundreds of MB/GB).
    box = _cover_crop_box(src_w, src_h, width, height)
    return rgba.crop(box).resize((width, height), Image.LANCZOS)


def _round_corners(image: Any, radius: int) -> Any:
    """Apply a rounded-corner alpha mask to an ``RGBA`` image."""
    from PIL import Image, ImageChops, ImageDraw

    if radius <= 0:
        return image
    width, height = image.size
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    try:
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    except Exception:  # noqa: BLE001 - older Pillow without rounded_rectangle
        return image
    existing = image.getchannel("A")
    image.putalpha(ImageChops.multiply(existing, mask))
    return image


def load_thumbnail(
    path: Any,
    width: Any,
    height: Any,
    *,
    radius: int = DEFAULT_THUMBNAIL_RADIUS,
) -> Optional[Any]:
    """Load ``path`` as an exact ``width``x``height`` rounded RGBA image.

    Aspect ratio is preserved by centre-cropping ("cover" fit, which may trim
    the source edges). Returns ``None`` for a missing/corrupt/unreadable file, a
    file above :data:`MAX_COVER_BYTES`, a target size outside ``(0,
    MAX_COVER_RESIZE_DIMENSION]``, or when Pillow is unavailable. Never raises.
    """
    try:
        target_w = int(width)
        target_h = int(height)
    except (TypeError, ValueError):
        return None
    if target_w <= 0 or target_h <= 0:
        return None
    if target_w > MAX_COVER_RESIZE_DIMENSION or target_h > MAX_COVER_RESIZE_DIMENSION:
        return None
    source = _as_path(path)
    if source is None:
        return None
    if not _within_size_limit(source):
        return None
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - Pillow missing in a stripped environment
        return None
    try:
        with Image.open(source) as handle:
            handle.load()
            thumb = _cover_fit(handle, target_w, target_h)
        corner = max(0, min(int(radius), target_w // 2, target_h // 2))
        return _round_corners(thumb, corner)
    except Exception:  # noqa: BLE001 - any decode failure means "no cover"
        return None


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

"""Cover artwork discovery and thumbnail loading for save tiles.

The module is intentionally self-contained and side-effect free:

* it is **read-only** — it only stats/reads existing files and never writes,
  creates, or mutates anything;
* it **never raises** — every public helper degrades to ``None``;
* it **does not import tkinter** — thumbnails come back as Pillow ``RGBA``
  images, and the UI layer wraps them in ``ImageTk.PhotoImage`` itself.

Cover resolution follows a three layer priority:

1. ``SaveEntry.cover_path`` — an icon found inside the save folder during the
   device scan (see :func:`find_embedded_cover`);
2. the user cover directory ``<library_root>/covers/<platform>/<name>.<ext>``
   (see :func:`user_cover_path`);
3. ``None`` — the UI then paints the pastel fallback face.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from .library import sanitize_name

# Supported cover/thumbnail extensions, in lookup order.
IMAGE_EXTENSIONS = ("png", "jpg", "jpeg", "webp", "bmp", "gif")

# Icon file names that consoles/backup tools drop inside a save folder. Matched
# case-insensitively, in this priority order, so ``ICON0.PNG`` (PSP) and
# ``sce_sys/icon0.png`` (Vita) both resolve.
EMBEDDED_ICON_NAMES = (
    "icon0.png",
    "icon0.jpg",
    "icon0.jpeg",
    "icon.png",
    "icon.jpg",
    "icon.jpeg",
    "icon.bmp",
    "cover.png",
    "cover.jpg",
    "cover.jpeg",
    "banner.png",
    "banner.jpg",
    "boxart.png",
    "boxart.jpg",
)

# Sub-directories probed inside a save folder, relative to the save itself.
_COVER_SUBDIRS = ("", "sce_sys", "media", "icon")

# Default corner radius for the rounded thumbnail produced by ``load_thumbnail``.
DEFAULT_THUMBNAIL_RADIUS = 12


def _as_path(value: Any) -> Optional[Path]:
    if value is None:
        return None
    try:
        return value if isinstance(value, Path) else Path(value)
    except (TypeError, ValueError, OSError):
        return None


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


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
        if _is_file(entry):
            by_name.setdefault(entry.name.lower(), entry)
    for name in EMBEDDED_ICON_NAMES:
        found = by_name.get(name)
        if found is not None:
            return found
    return None


def _sibling_cover(save_file: Path) -> Optional[Path]:
    """Look for ``<stem>.<ext>`` or a known icon next to a raw ``.sav`` file."""
    parent = save_file.parent
    entries = _dir_entries(parent)
    if not entries:
        return None
    by_name = {}
    for entry in entries:
        if _is_file(entry):
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


def find_embedded_cover(save_path: Any, platform: Optional[str] = None) -> Optional[Path]:
    """Find an icon embedded in a save folder (or next to a raw save file).

    ``platform`` is accepted for callers that want to hint the layout, but the
    lookup is layout-agnostic and based on well-known icon file names. Returns
    the first match, or ``None``. Never raises.
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
    for sub in _COVER_SUBDIRS:
        directory = path if not sub else path / sub
        found = _match_icon(directory)
        if found is not None:
            return found
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
    Only existing files are returned.
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
            if _is_file(candidate):
                by_name.setdefault(candidate.name.lower(), candidate)
        for stem in stems:
            for ext in IMAGE_EXTENSIONS:
                found = by_name.get(f"{stem}.{ext}".lower())
                if found is not None:
                    return found
        return None
    except Exception:  # noqa: BLE001 - a cover lookup must never break the UI
        return None


def resolve_cover(entry: Any, library_root: Any) -> Optional[Path]:
    """Resolve the cover for ``entry`` using the three layer priority."""
    try:
        if entry is None:
            return None
        embedded = _as_path(getattr(entry, "cover_path", None))
        if embedded is not None and _is_file(embedded):
            return embedded
        return user_cover_path(entry, library_root)
    except Exception:  # noqa: BLE001 - a cover lookup must never break the UI
        return None


def _cover_fit(image: Any, width: int, height: int) -> Any:
    """Scale+crop ``image`` to exactly ``width``x``height`` keeping its ratio."""
    from PIL import Image

    rgba = image.convert("RGBA")
    src_w, src_h = rgba.size
    if src_w <= 0 or src_h <= 0:
        return Image.new("RGBA", (width, height), (0, 0, 0, 0))
    scale = max(width / src_w, height / src_h)
    scaled = (max(1, int(round(src_w * scale))), max(1, int(round(src_h * scale))))
    resized = rgba.resize(scaled, Image.LANCZOS)
    left = max(0, (scaled[0] - width) // 2)
    top = max(0, (scaled[1] - height) // 2)
    return resized.crop((left, top, left + width, top + height))


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

    Aspect ratio is preserved by centre-cropping ("cover" fit). Returns ``None``
    for a missing/corrupt/unreadable file, a non-positive size, or when Pillow
    is unavailable. Never raises.
    """
    try:
        target_w = int(width)
        target_h = int(height)
    except (TypeError, ValueError):
        return None
    if target_w <= 0 or target_h <= 0:
        return None
    source = _as_path(path)
    if source is None:
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
    "DEFAULT_THUMBNAIL_RADIUS",
    "find_embedded_cover",
    "user_cover_path",
    "resolve_cover",
    "load_thumbnail",
]

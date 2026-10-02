"""Thumbnail generation and resizing helpers for save tiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

MAX_COVER_BYTES = 8 * 1024 * 1024
MAX_COVER_RESIZE_DIMENSION = 4096
DEFAULT_THUMBNAIL_RADIUS = 12


def _as_path(value: Any) -> Optional[Path]:
    if value is None:
        return None
    try:
        return value if isinstance(value, Path) else Path(value)
    except (TypeError, ValueError, OSError):
        return None


def _within_size_limit(path: Path) -> bool:
    """True when ``path`` is a regular file no larger than ``MAX_COVER_BYTES``."""
    try:
        if not path.is_file():
            return False
        return path.stat().st_size <= MAX_COVER_BYTES
    except (OSError, ValueError):
        return False


def _cover_crop_box(src_w: int, src_h: int, target_w: int, target_h: int) -> tuple:
    """Largest centred source rect whose aspect matches ``target_w:target_h``.

    Returns integer ``(left, top, right, bottom)`` pixel bounds.
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
    except Exception:  # noqa: BLE001
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

    Aspect ratio is preserved by centre-cropping. Returns ``None`` for missing/corrupt
    files, wrong aspect or sizes exceeding bounds.
    """
    try:
        w = int(width)
        h = int(height)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    if w > MAX_COVER_RESIZE_DIMENSION or h > MAX_COVER_RESIZE_DIMENSION:
        return None
    p = _as_path(path)
    if p is None or not _within_size_limit(p):
        return None

    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None

    try:
        with Image.open(p) as img:
            img = ImageOps.exif_transpose(img)
            fitted = _cover_fit(img, w, h)
            return _round_corners(fitted, radius)
    except Exception:  # noqa: BLE001
        return None

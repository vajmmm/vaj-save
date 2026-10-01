"""vaj-save Archive Desk design tokens and pure layout helpers.

This module is intentionally free of GUI toolkit imports so it can be unit
tested without a display. ``qt_ui`` consumes these tokens and layout helpers.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, Optional

FONT_FAMILY = "PingFang SC" if sys.platform == "darwin" else ("Microsoft YaHei" if sys.platform == "win32" else "Noto Sans CJK SC")

# --- tokens -----------------------------------------------------------------------

# Switch Basic White surface ramp: light gray page > soft panel > subtle alt,
# white rows, near-black ink, system blue accent. The functional colours stay on
# the Apple system palette (`success`/`warning`/`danger`) so text status reads
# the same as the rest of the OS chrome.
SWITCH: Dict[str, str] = {
    "bg": "#ebebeb",
    "surface": "#f2f2f2",
    "surface_alt": "#e7e7e7",
    "card": "#ffffff",
    "window": "#ffffff",
    "panel": "#ffffff",
    "panel_alt": "#f7f9fc",
    "text": "#2d2d2d",
    "text_strong": "#1f2733",
    "ink": "#14233b",
    "muted": "#8b8b8b",
    "muted_strong": "#687383",
    "line": "#d6d6d6",
    "line_soft": "#e3e6ea",
    "border_soft": "#e1e7f0",
    "line_strong": "#b0b0b0",
    "hover": "#e2e2e2",
    "selected": "#e4f1ff",
    "selected_soft": "#eaf3ff",
    "accent": "#0a84ff",
    "accent_hover": "#409cff",
    "on_accent": "#ffffff",
    "success": "#30d158",
    "warning": "#ff9f0a",
    "danger": "#ff453a",
    "status_blue": "#2563eb",
    "status_green": "#16a34a",
    "status_orange": "#f59e0b",
    # Mist-silver gallery surfaces.  These stay neutral so box art remains the
    # strongest colour in the room; the system blue is still reserved for the
    # primary action and focused selection.
    "fog_top": "#f4f6f9",
    "fog_canvas": "#edf1f5",
    "fog_panel": "#f8fafc",
    "fog_glass": "#f5f8fb",
    "shadow_soft": "#cbd2dc",
    "shadow_deep": "#aeb7c3",
    "shelf_face": "#d9dee5",
    "shelf_edge": "#b9c1cc",
    "shelf_highlight": "#f9fafc",
    "dock_selected": "#dcecff",
}

# Module-level aliases keep the token names greppable from the UI code without
# reaching through the dict every time.
HOVER = SWITCH["hover"]
LINE_STRONG = SWITCH["line_strong"]
ACCENT_HOVER = SWITCH["accent_hover"]

# Canonical handheld platform identity colors used by the gallery.
PLATFORM_COLORS: Dict[str, str] = {
    "all": "#0a84ff",
    "psp": "#64d2ff",
    "vita": "#63e6be",
    "switch": "#ff3c28",
    "3ds": "#ffd60a",
    "nds": "#bf5af2",
    "gb": "#9aa56a",
    "gbc": "#ff6b8a",
    "gba": "#30d158",
}

# Legacy row metrics remain exported for integrations that still import them;
# the Qt gallery uses the gallery metrics below.
ROW_HEIGHT = 70
ROW_COVER = 52
ROW_COVER_RADIUS = 6
ROW_PIP_WIDTH = 3

# Responsive gallery metrics. The card width is intentionally compact enough
# to keep four covers visible in the centre column at the default window size.
GALLERY_CARD_WIDTH = 208
GALLERY_CELL_HEIGHT = 396
GALLERY_COVER_WIDTH = 184
GALLERY_DISPLAY_HEIGHT = 300
# Combined height of the visible shelf face and its front lip.  The soft
# projection is painted below this baseline and does not consume the card hit
# target, matching the substantial floating shelf in the reference.
GALLERY_SHELF_HEIGHT = 38
# The rear edge of the shelf is recessed from the front edge, while the shadow
# continues past the lip as a broad, low-contrast gradient.  These geometry
# tokens are shared by the live Canvas and the offline visual preview.
GALLERY_SHELF_INSET = 48
GALLERY_SHELF_SHADOW_HEIGHT = 44
DOCK_WIDTH = 116
DRAWER_WIDTH = 430
DRAWER_ANIMATION_STEPS = 8
DRAWER_ANIMATION_MS = 16

# Per-platform physical case proportions. Handheld boxes are near-square and
# sit lower than the tall Switch/PSP plastic cases, so the gallery no longer
# forces every platform into the same Switch-shaped white frame.
GALLERY_CASE_ASPECT: Dict[str, float] = {
    "switch": 0.63,
    "psp": 0.74,
    "vita": 0.74,
    "3ds": 0.90,
    "nds": 0.92,
    "gba": 0.93,
    "gb": 0.95,
    "gbc": 0.95,
}
# Relative height against ``GALLERY_CELL_HEIGHT``/``CASE_H``. Switch and PSP
# render at full height; the near-square handheld boxes are intentionally
# smaller so they read as different physical cartridges instead of clipped
# Switch cases.
GALLERY_CASE_SCALE: Dict[str, float] = {
    "switch": 1.0,
    "psp": 1.0,
    "vita": 1.0,
    "3ds": 0.70,
    "nds": 0.68,
    "gba": 0.66,
    "gb": 0.62,
    "gbc": 0.62,
}
GALLERY_CASE_DEFAULT_ASPECT = 0.78
GALLERY_CASE_DEFAULT_SCALE = 0.80
# Horizontal breathing room kept between a case and the cell edge when the
# aspect-derived width would otherwise overflow.
GALLERY_CASE_WIDTH_INSET = 12

# Detail drawer cover box. The control tracks the current platform's box
# aspect (or the real portrait artwork aspect when one is available).
DETAIL_COVER_MAX_WIDTH = 140
DETAIL_COVER_MAX_HEIGHT = 196
DETAIL_COVER_ASPECT_RANGE = (0.55, 1.05)

STATUS_LABELS: Dict[str, str] = {
    "new": "新",
    "changed": "有变化",
    "unchanged": "已备份",
    "checking": "核对中",
}


# --- color helpers ----------------------------------------------------------------


def _parse_hex(color: str) -> tuple[int, int, int]:
    value = str(color).strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        raise ValueError(f"invalid hex color: {color!r}")
    try:
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except ValueError as exc:  # non-hex characters
        raise ValueError(f"invalid hex color: {color!r}") from exc


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    """Parse ``#rgb``/``#rrggbb`` into an ``(r, g, b)`` tuple."""
    return _parse_hex(color)


def rgb_to_hex(rgb: Any) -> str:
    """Render an ``(r, g, b)`` sequence as a lowercase ``#rrggbb`` string.

    Channel values are clamped to ``0..255`` so saturating blends never raise.
    """
    red, green, blue = rgb

    def _clamp(value: Any) -> int:
        return max(0, min(255, int(round(float(value)))))

    return "#{:02x}{:02x}{:02x}".format(_clamp(red), _clamp(green), _clamp(blue))


def lighten(color: str, amount: float = 0.1) -> str:
    """Blend ``color`` toward white by ``amount`` (0..1)."""
    return mix(color, "#ffffff", amount)


def darken(color: str, amount: float = 0.1) -> str:
    """Blend ``color`` toward black by ``amount`` (0..1)."""
    return mix(color, "#000000", amount)


def mix(color_a: str, color_b: str, t: float) -> str:
    """Linear blend between two hex colors. ``t=0`` -> ``color_a``, ``t=1`` -> ``color_b``.

    ``t`` is clamped to ``[0, 1]``; 3-digit hex shorthand is accepted.
    """
    if t < 0:
        t = 0.0
    elif t > 1:
        t = 1.0
    ar, ag, ab = _parse_hex(color_a)
    br, bg, bb = _parse_hex(color_b)

    def _blend(a: int, b: int) -> int:
        return int(a + (b - a) * t + 0.5)

    return "#{:02x}{:02x}{:02x}".format(_blend(ar, br), _blend(ag, bg), _blend(ab, bb))


# --- gallery / detail layout helpers -------------------------------------------


def gallery_case_size(
    platform: Any,
    *,
    cell_width: float,
    case_height: float,
) -> tuple[int, int]:
    """Return the ``(width, height)`` of a platform's packaging in the gallery.

    The height is derived from ``case_height * scale`` and the width from the
    platform aspect. If the aspect-derived width would overflow the cell, the
    box is scaled down proportionally instead of being clamped flat (which used
    to make every platform the same width).
    """
    key = str(platform or "").strip().lower()
    aspect = GALLERY_CASE_ASPECT.get(key, GALLERY_CASE_DEFAULT_ASPECT)
    scale = GALLERY_CASE_SCALE.get(key, GALLERY_CASE_DEFAULT_SCALE)
    height = max(1.0, float(case_height) * scale)
    width = height * aspect
    max_width = max(1.0, float(cell_width) - GALLERY_CASE_WIDTH_INSET)
    if width > max_width:
        width = max_width
        height = width / aspect
    return int(round(width)), int(round(height))


def detail_cover_size(
    platform: Any,
    *,
    max_width: float = DETAIL_COVER_MAX_WIDTH,
    max_height: float = DETAIL_COVER_MAX_HEIGHT,
    cover_aspect: Optional[float] = None,
) -> tuple[int, int]:
    """Return the portrait control size for the detail drawer cover.

    ``cover_aspect`` may carry the real artwork ratio; when it is a plausible
    portrait/near-square value it wins over the platform default so a square
    NDS cover is not letterboxed in a tall Switch frame.
    """
    key = str(platform or "").strip().lower()
    aspect: Optional[float] = None
    if cover_aspect is not None:
        try:
            candidate = float(cover_aspect)
        except (TypeError, ValueError):
            candidate = 0.0
        low, high = DETAIL_COVER_ASPECT_RANGE
        if low <= candidate <= high:
            aspect = candidate
    if aspect is None:
        aspect = GALLERY_CASE_ASPECT.get(key, GALLERY_CASE_DEFAULT_ASPECT)
    if aspect <= 0:
        aspect = GALLERY_CASE_DEFAULT_ASPECT
    height = max(1.0, float(max_height))
    width = height * aspect
    max_width_value = max(1.0, float(max_width))
    if width > max_width_value:
        width = max_width_value
        height = width / aspect
    return int(round(width)), int(round(height))


# --- row helpers ------------------------------------------------------------------


def status_label(status: Any) -> str:
    """Human-readable status text for a backup status string or status object."""
    key = getattr(status, "status", status) or "new"
    return STATUS_LABELS.get(key, key)


def save_row(entry: Any, status: Any, *, starred: bool = False) -> Dict[str, Any]:
    """Describe one display item for the responsive save gallery.

    ``status`` may be a status string or any object exposing a ``.status``
    attribute (e.g. ``library.SaveBackupStatus``), keeping this helper pure and
    independent of the state layer. The item intentionally carries plain data;
    geometry and shelf rendering stay in the UI view layer.
    """
    status_key = getattr(status, "status", status) or "new"
    title = entry.display_name or entry.path
    subtitle_bits = [bit for bit in (entry.title_id, entry.slot, entry.user) if bit]
    accent = PLATFORM_COLORS.get(entry.platform, SWITCH["accent"])
    return {
        "title": title,
        "subtitle": " · ".join(subtitle_bits),
        "platform": entry.platform,
        "accent": accent,
        "status": status_key,
        "status_label": STATUS_LABELS.get(status_key, status_key),
        "starred": bool(starred),
    }

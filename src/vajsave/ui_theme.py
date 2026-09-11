"""Nintendo Switch HOME "Basic White" design tokens and pure layout helpers.

This module is intentionally free of Tkinter imports so it can be unit tested
without a display. ``app_ui`` consumes these tokens/helpers to render the quiet
single-column save list and the right-hand inspector.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

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
    "text": "#2d2d2d",
    "muted": "#8b8b8b",
    "line": "#d6d6d6",
    "line_strong": "#b0b0b0",
    "hover": "#e2e2e2",
    "accent": "#0a84ff",
    "accent_hover": "#409cff",
    "on_accent": "#ffffff",
    "success": "#30d158",
    "warning": "#ff9f0a",
    "danger": "#ff453a",
}

# Module-level aliases keep the token names greppable from the UI code without
# reaching through the dict every time.
HOVER = SWITCH["hover"]
LINE_STRONG = SWITCH["line_strong"]
ACCENT_HOVER = SWITCH["accent_hover"]

# Canonical handheld platform identity colors (shared with DESIGN.md).
PLATFORM_COLORS: Dict[str, str] = {
    "all": "#0a84ff",
    "psp": "#64d2ff",
    "vita": "#63e6be",
    "switch": "#ff3c28",
    "3ds": "#ffd60a",
    "nds": "#bf5af2",
    "gba": "#30d158",
}

# Single-column save-list row metrics. Rows are deliberately compact and flat:
# a small platform pip, an optional tiny cover square, a title and a text status.
ROW_HEIGHT = 44
ROW_COVER = 32
ROW_COVER_RADIUS = 6
ROW_PIP_WIDTH = 3

STATUS_LABELS: Dict[str, str] = {
    "new": "新",
    "changed": "有变化",
    "unchanged": "已备份",
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


# --- row helpers ------------------------------------------------------------------


def status_label(status: Any) -> str:
    """Human-readable status text for a backup status string or status object."""
    key = getattr(status, "status", status) or "new"
    return STATUS_LABELS.get(key, key)


def save_row(entry: Any, status: Any, *, starred: bool = False) -> Dict[str, Any]:
    """Describe one row of the single-column save list.

    ``status`` may be a status string or any object exposing a ``.status``
    attribute (e.g. ``library.SaveBackupStatus``), keeping this helper pure and
    independent of the state layer. The row intentionally carries only plain
    text data — no pastel face, monogram, pill or geometry.
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

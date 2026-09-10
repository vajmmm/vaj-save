"""Nintendo Switch HOME "Basic White" design tokens and pure layout helpers.

This module is intentionally free of Tkinter imports so it can be unit tested
without a display. ``app_ui`` consumes these tokens/helpers to render the white
card-tile HOME menu.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

# --- tokens -----------------------------------------------------------------------

# Switch Basic White surface ramp: light gray page > soft panel > subtle alt,
# white cards on top, near-black ink, system blue accent with a brighter ring.
SWITCH: Dict[str, str] = {
    "bg": "#ebebeb",
    "surface": "#f2f2f2",
    "surface_alt": "#e7e7e7",
    "card": "#ffffff",
    "text": "#2d2d2d",
    "muted": "#6f6f6f",
    "line": "#d6d6d6",
    "accent": "#0a84ff",
    "ring": "#00a2ff",
    "on_accent": "#ffffff",
    "success": "#34c759",
    "warning": "#ff9500",
    "danger": "#ff3b30",
}

# Canonical handheld platform identity colors (shared with DESIGN.md).
PLATFORM_COLORS: Dict[str, str] = {
    "all": "#0a84ff",
    "psp": "#64d2ff",
    "vita": "#63e6be",
    "switch": "#ff453a",
    "3ds": "#ffd60a",
    "nds": "#bf5af2",
    "gba": "#30d158",
}

DEFAULT_TILE_WIDTH = 156
DEFAULT_TILE_GAP = 14

_STATUS_PILL_SPECS: Dict[str, Dict[str, str]] = {
    "new": {"label": "新", "fg": SWITCH["accent"]},
    "changed": {"label": "有变化", "fg": SWITCH["warning"]},
    "unchanged": {"label": "已备份", "fg": SWITCH["success"]},
}

_CJK_MIN_CODEPOINT = 0x2E80
_HEX_TOKEN = re.compile(r"[0-9A-Za-z]+")


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


# --- layout helpers ---------------------------------------------------------------


def grid_columns(
    width: float,
    tile_width: int = DEFAULT_TILE_WIDTH,
    gap: int = DEFAULT_TILE_GAP,
) -> int:
    """How many ``tile_width`` tiles (plus ``gap``) fit across ``width`` px.

    Always returns at least 1 so an unlaid-out canvas still renders something.
    """
    try:
        available = int(width)
    except (TypeError, ValueError):
        return 1
    step = max(1, int(tile_width) + int(gap))
    return max(1, (available + int(gap)) // step)


# --- text helpers -----------------------------------------------------------------


def monogram(text: Optional[str]) -> str:
    """A short glyph used as the tile face badge.

    CJK titles keep their leading ideograph; latin titles use the initials of up
    to two words (``Persona 4`` -> ``P4``). Empty/unusable input becomes ``?``.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return "?"
    if ord(cleaned[0]) >= _CJK_MIN_CODEPOINT:
        return cleaned[0]
    tokens = _HEX_TOKEN.findall(cleaned)
    if not tokens:
        return "?"
    letters = tokens[0][0]
    if len(tokens) > 1:
        letters += tokens[1][0]
    return letters.upper()


# --- tile face helpers ------------------------------------------------------------


def status_pill(status: Optional[str]) -> Dict[str, str]:
    """Foreground/background/label for a backup status pill.

    Unknown statuses fall back to a muted grey pill labelled with the raw status.
    """
    spec = _STATUS_PILL_SPECS.get(status)
    if spec is None:
        label = status if status else "未知"
        return {
            "key": status or "unknown",
            "label": label,
            "fg": SWITCH["muted"],
            "bg": SWITCH["surface_alt"],
        }
    return {
        "key": status,
        "label": spec["label"],
        "fg": spec["fg"],
        "bg": mix(spec["fg"], SWITCH["card"], 0.86),
    }


def tile_face(entry: Any, status: Any, *, starred: bool = False) -> Dict[str, Any]:
    """Describe one save tile.

    ``status`` may be a status string or any object exposing a ``.status``
    attribute (e.g. ``library.SaveBackupStatus``), keeping this helper pure and
    independent of the state layer.
    """
    status_key = getattr(status, "status", status) or "new"
    title = entry.display_name or entry.path
    subtitle_bits = [bit for bit in (entry.title_id, entry.slot, entry.user) if bit]
    return {
        "title": title,
        "subtitle": " · ".join(subtitle_bits),
        "monogram": monogram(entry.display_name),
        "accent": PLATFORM_COLORS.get(entry.platform, SWITCH["accent"]),
        "platform": entry.platform,
        "status": status_key,
        "pill": status_pill(status_key),
        "starred": bool(starred),
    }

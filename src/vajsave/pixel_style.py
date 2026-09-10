"""Pixel-art HUD helpers for the desktop app."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageTk


PALETTE = {
    "bg": "#140c28",
    "panel": "#1e1236",
    "panel2": "#2a1848",
    "ink": "#f4e8c1",
    "muted": "#a894c8",
    "gold": "#ffd24a",
    "shadow": "#07040f",
    "hi": "#6a4cff",
    "psp": "#4a9fff",
    "vita": "#3ee0c0",
    "switch": "#ff4d6a",
    "3ds": "#ffcc33",
    "nds": "#9b7bff",
    "gba": "#6fbf73",
    "all": "#ffd24a",
}


def assets_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "assets"
    return Path(__file__).resolve().parents[2] / "assets"


def pixel_font_path() -> Path:
    return assets_dir() / "fonts" / "PressStart2P-Regular.ttf"


def _cjk_font_path() -> Optional[Path]:
    candidates = [
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_font(size: int, *, cjk: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if cjk:
        cjk_path = _cjk_font_path()
        if cjk_path is not None:
            try:
                return ImageFont.truetype(str(cjk_path), size=size)
            except OSError:
                pass
    path = pixel_font_path()
    if path.is_file():
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def render_text(
    text: str,
    size: int = 12,
    fill: str = "#f4e8c1",
    *,
    cjk: bool = False,
) -> ImageTk.PhotoImage:
    font = load_font(size, cjk=cjk)
    dummy = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.textbbox((0, 0), text, font=font)
    width = max(1, bbox[2] - bbox[0] + 2)
    height = max(1, bbox[3] - bbox[1] + 2)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((-bbox[0] + 1, -bbox[1] + 1), text, font=font, fill=fill)
    return ImageTk.PhotoImage(img)


def bevel_rect(
    canvas,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    fill: str,
    light: str = "#7d6cff",
    dark: str = "#07040f",
) -> None:
    canvas.create_rectangle(x1, y1, x2, y2, fill=dark, outline="")
    canvas.create_rectangle(x1, y1, x2 - 2, y2 - 2, fill=fill, outline="")
    canvas.create_line(x1, y1, x2 - 2, y1, fill=light)
    canvas.create_line(x1, y1, x1, y2 - 2, fill=light)
    canvas.create_line(x1, y2 - 2, x2 - 2, y2 - 2, fill=dark)
    canvas.create_line(x2 - 2, y1, x2 - 2, y2 - 2, fill=dark)


def hex_to_rgb(value: str) -> Tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)

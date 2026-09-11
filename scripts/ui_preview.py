#!/usr/bin/env python3
"""Headless preview renderer for the vaj-save Switch "Basic White" UI.

Builds the real Tk application against a synthetic demo device (no window
interaction, no filesystem scan, no ``mainloop``) and writes preview images to
``build/ui-preview/``.

Two independent renderers are used so the script always leaves something behind:

* ``home-preview.png`` is an **illustrative schematic** drawn with Pillow from the
  same ``ui_theme`` tokens and ``save_row`` data the live app uses — it is *not*
  a screenshot of the running widgets. It needs no Ghostscript and is stamped
  with a "示意图 · 非真实截图" badge so it can never be mistaken for one.
* ``save-list.eps`` is a real capture, taken from the live Tk save-list canvas via
  ``Canvas.postscript`` and converted to PNG through Pillow/Ghostscript when a
  working ``gs`` is available. When Ghostscript is missing or broken the ``.eps``
  file is kept and the script still exits 0.

Run with::

    python3 scripts/ui_preview.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SRC = PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from vajsave.app_state import PLATFORM_LABELS, PLATFORM_ORDER, AppState  # noqa: E402
from vajsave.app_ui import build_app  # noqa: E402
from vajsave.covers import load_thumbnail, resolve_cover  # noqa: E402
from vajsave.models import SaveEntry, ScanResult, VolumeInfo  # noqa: E402
from vajsave.ui_theme import (  # noqa: E402
    PLATFORM_COLORS,
    ROW_COVER,
    ROW_COVER_RADIUS,
    ROW_HEIGHT,
    ROW_PIP_WIDTH,
    SWITCH,
    mix,
    save_row,
)

# Demo catalogue: a save from every platform the scanner understands.
DEMO_SAVES = [
    ("psp", "Monster Hunter Portable 3rd", "ULJM05800", "1"),
    ("vita", "Persona 4 Golden", "PCSE00120", None),
    ("switch", "The Legend of Zelda", "01007EF00011E000", "Auto"),
    ("3ds", "Fire Emblem Awakening", "CTR-P-AFRE", None),
    ("nds", "Pokemon Black", "IRBO", None),
    ("gba", "Metroid Fusion", None, None),
]


# --- demo state -------------------------------------------------------------------


def _make_demo_cover(root: Path, platform: str, name: str, color: str) -> Optional[Path]:
    """Write a small demo cover PNG so the preview shows real artwork."""
    try:
        from PIL import Image

        cover_dir = root / platform
        cover_dir.mkdir(parents=True, exist_ok=True)
        path = cover_dir / f"{name}.png"
        Image.new("RGB", (128, 160), color).save(path)
        return path
    except Exception:  # noqa: BLE001 - a missing cover just falls back to the placeholder
        return None


def _demo_scan(root: Path) -> ScanResult:
    saves = []
    for index, (platform, name, title_id, slot) in enumerate(DEMO_SAVES):
        entry = SaveEntry(
            platform=platform,
            source_id=f"demo_{platform}",
            display_name=name,
            path=str(Path(root) / platform / (title_id or name)),
            title_id=title_id,
            slot=slot,
        )
        if index < 3:
            # Illustrate the embedded-cover layer for the first few saves.
            cover = _make_demo_cover(
                root, platform, (title_id or name).replace("/", "_"),
                PLATFORM_COLORS.get(platform, SWITCH["accent"]),
            )
            if cover is not None:
                entry.cover_path = str(cover)
        saves.append(entry)
    return ScanResult(root_path=str(root), platform="demo", sources=[], saves=saves, warnings=[])


class _DemoVolumeProvider:
    def __init__(self, volume: VolumeInfo) -> None:
        self._volume = volume

    def list_volumes(self) -> List[VolumeInfo]:
        return [self._volume]


def build_demo_state(scandir: Path) -> AppState:
    volume = VolumeInfo(name="DEMO 掌机", mount_point=scandir)
    state = AppState(
        provider=_DemoVolumeProvider(volume),
        scan_fn=_demo_scan,
        library_root=scandir / "lib",
    )
    state.refresh_volumes()
    state.select_mount(volume.mount_point)
    return state


# --- live Tk capture --------------------------------------------------------------


def build_offscreen_app(state: AppState):
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    root.geometry("1180x740")
    app = build_app(state=state, root=root)
    root.update_idletasks()
    return root, app


def _rasterize(eps_path: Path, png_path: Path) -> bool:
    """EPS -> PNG through Pillow/Ghostscript. Returns False when gs is unusable."""
    try:
        from PIL import Image

        with Image.open(eps_path) as image:
            image.load()
            image.convert("RGB").save(png_path)
        return True
    except Exception as exc:  # noqa: BLE001 - any gs/PIL failure means "degrade"
        print(f"[ui-preview] Ghostscript 不可用，保留 {eps_path.name}（{exc}）", file=sys.stderr)
        return False


def capture_canvases(app, out_dir: Path) -> List[Path]:
    written: List[Path] = []
    for name, canvas in (("save-list", app.save_list),):
        try:
            postscript = canvas.postscript(colormode="color")
        except Exception as exc:  # noqa: BLE001
            print(f"[ui-preview] {name}: postscript 不可用（{exc}）", file=sys.stderr)
            continue
        eps_path = out_dir / f"{name}.eps"
        eps_path.write_text(postscript, encoding="utf-8")
        png_path = out_dir / f"{name}.png"
        written.append(png_path if _rasterize(eps_path, png_path) else eps_path)
    return written


# --- offline Pillow render --------------------------------------------------------


def _load_font(size: int, bold: bool = False):
    from PIL import ImageFont

    candidates: List[Path] = [
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    windir = os.environ.get("WINDIR")
    if windir:
        candidates.insert(0, Path(windir) / "Fonts" / "msyh.ttc")
    for path in candidates:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except Exception:  # noqa: BLE001 - try the next candidate
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _rounded(draw, box, radius: int, fill: str, outline: Optional[str] = None, width: int = 1) -> None:
    try:
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
    except Exception:  # noqa: BLE001 - older Pillow without rounded_rectangle
        draw.rectangle(box, fill=fill, outline=outline)


def render_home_preview(out_dir: Path, state: AppState) -> Path:
    """Draw a representative screen from the real theme tokens and row data."""
    from PIL import Image, ImageDraw

    width, height = 1180, 740
    pad = 24
    top_h = 56
    image = Image.new("RGB", (width, height), SWITCH["bg"])
    draw = ImageDraw.Draw(image)
    font_title = _load_font(17, bold=True)
    font_heading = _load_font(15, bold=True)
    font_body = _load_font(13)
    font_small = _load_font(11)
    font_status = _load_font(12)

    # Top status bar: identity + subtitle on the left, stats on the right.
    draw.rectangle([0, 0, width, top_h], fill=SWITCH["surface"])
    draw.text((pad, 12), "vaj-save", font=font_title, fill=SWITCH["text"], anchor="la")
    draw.text((pad, 34), "把掌机存档备份下来，按版本管理", font=font_small, fill=SWITCH["muted"], anchor="la")

    # This drawing is a mock, not a rasterization of the live widgets: say so.
    note = "示意图 · 非真实截图"
    note_w = 20 + 11 * len(note)
    _rounded(draw, [width / 2 - note_w / 2, 16, width / 2 + note_w / 2, 40], 6, SWITCH["surface_alt"], SWITCH["line"])
    draw.text((width / 2, 28), note, font=font_small, fill=SWITCH["muted"], anchor="mm")
    stats = state.collection_stats()
    draw.text(
        (width - pad, 28),
        f"已备份 {stats['games']} 款游戏 · {stats['versions']} 个版本",
        font=font_small,
        fill=SWITCH["muted"],
        anchor="rm",
    )

    # Search row.
    search_y = top_h + 14
    draw.text((pad, search_y + 8), "搜索", font=font_small, fill=SWITCH["muted"], anchor="la")
    _rounded(draw, [pad + 52, search_y, width - pad, search_y + 30], 6, SWITCH["card"], SWITCH["line"])

    body_top = search_y + 46
    body_bottom = height - 96

    # Left column: platform filters.
    left = [pad, body_top, pad + 220, body_bottom]
    _rounded(draw, left, 10, SWITCH["surface"])
    draw.text((left[0] + 16, body_top + 14), "机种", font=font_small, fill=SWITCH["muted"], anchor="la")
    counts = state.platform_counts()
    for i, key in enumerate(PLATFORM_ORDER):
        row_y = body_top + 44 + i * 34
        selected = key == state.selected_platform
        if selected:
            _rounded(draw, [left[0] + 8, row_y, left[2] - 8, row_y + 30], 6, mix(SWITCH["accent"], SWITCH["card"], 0.84))
        draw.rectangle([left[0] + 14, row_y + 7, left[0] + 17, row_y + 23], fill=PLATFORM_COLORS.get(key, SWITCH["accent"]))
        draw.text((left[0] + 26, row_y + 15), PLATFORM_LABELS.get(key, key), font=font_body, fill=SWITCH["text"], anchor="lm")
        count = len(state.all_saves()) if key == "all" else counts.get(key, 0)
        draw.text((left[2] - 16, row_y + 15), str(count), font=font_small, fill=SWITCH["muted"], anchor="rm")

    # Middle column: single-column save list rows.
    mid = [pad + 234, body_top, width - pad - 360, body_bottom]
    draw.text((mid[0], body_top + 14), "游戏", font=font_small, fill=SWITCH["muted"], anchor="la")
    row_top = body_top + 34
    row_right = mid[2]
    visible = state.visible_saves()
    for i, save in enumerate(visible):
        status = state.save_status(save)
        row = save_row(save, status)
        y1 = row_top + i * ROW_HEIGHT
        y2 = y1 + ROW_HEIGHT
        if i == 0:
            # Illustrate the selection tint on the top row.
            _rounded(draw, [mid[0], y1 + 2, row_right, y2 - 2], 6, mix(SWITCH["accent"], SWITCH["card"], 0.86))
        # 3px platform pip.
        draw.rectangle([mid[0], y1 + 8, mid[0] + ROW_PIP_WIDTH, y2 - 8], fill=row["accent"])
        # Cover square or light-grey placeholder.
        cover_x = mid[0] + 14
        cover_y = y1 + (ROW_HEIGHT - ROW_COVER) // 2
        thumb = None
        cover_path = resolve_cover(save, state.library_root)
        if cover_path is not None:
            thumb = load_thumbnail(cover_path, ROW_COVER, ROW_COVER, radius=ROW_COVER_RADIUS)
        if thumb is not None:
            image.paste(thumb, (cover_x, cover_y), thumb)
        else:
            _rounded(draw, [cover_x, cover_y, cover_x + ROW_COVER, cover_y + ROW_COVER], ROW_COVER_RADIUS, SWITCH["surface_alt"])
        # Title (and optional subtitle) then right-aligned status text.
        text_x = cover_x + ROW_COVER + 12
        if row["subtitle"]:
            draw.text((text_x, y1 + ROW_HEIGHT * 0.34), row["title"], font=font_body, fill=SWITCH["text"], anchor="lm")
            draw.text((text_x, y1 + ROW_HEIGHT * 0.70), row["subtitle"], font=font_small, fill=SWITCH["muted"], anchor="lm")
        else:
            draw.text((text_x, y1 + ROW_HEIGHT / 2), row["title"], font=font_body, fill=SWITCH["text"], anchor="lm")
        draw.text((row_right - 12, y1 + ROW_HEIGHT / 2), row["status_label"], font=font_status, fill=SWITCH["muted"], anchor="rm")
        draw.line([mid[0], y2, row_right, y2], fill=SWITCH["line"])

    # Right column: inspector.
    right = [width - pad - 360, body_top, width - pad, body_bottom]
    _rounded(draw, right, 10, SWITCH["surface"])
    draw.text((right[0] + 16, body_top + 14), "详情", font=font_small, fill=SWITCH["muted"], anchor="la")
    btn_x = right[2] - 16
    for label, accent in (("导出 ZIP", False), ("恢复", False), ("备份", True)):
        text_w = 12 + 8 * len(label)
        btn_x -= text_w
        fill = SWITCH["accent"] if accent else SWITCH["surface_alt"]
        color = SWITCH["on_accent"] if accent else SWITCH["text"]
        _rounded(draw, [btn_x, body_top + 8, btn_x + text_w, body_top + 34], 6, fill)
        draw.text((btn_x + text_w / 2, body_top + 21), label, font=font_small, fill=color, anchor="mm")
        btn_x -= 6
    selected_name = visible[0].display_name if visible else "未选择游戏"
    draw.text((right[0] + 16, body_top + 48), selected_name, font=font_heading, fill=SWITCH["text"], anchor="la")
    for j, (label, value) in enumerate((("机种", "PSP"), ("状态", "新"), ("卡上时间", "—"), ("上次备份", "—"), ("路径", "…"))):
        fy = body_top + 82 + j * 20
        draw.text((right[0] + 16, fy), label, font=font_small, fill=SWITCH["muted"], anchor="la")
        draw.text((right[0] + 90, fy), value, font=font_small, fill=SWITCH["text"], anchor="la")
    versions_top = body_top + 82 + 5 * 20 + 12
    draw.text((right[0] + 16, versions_top), "版本", font=font_small, fill=SWITCH["muted"], anchor="la")
    _rounded(draw, [right[0] + 16, versions_top + 18, right[2] - 16, right[3] - 54], 6, SWITCH["card"], SWITCH["line"])
    draw.text((right[0] + 16, right[3] - 42), "备注", font=font_small, fill=SWITCH["muted"], anchor="la")
    _rounded(draw, [right[0] + 16, right[3] - 26, right[2] - 16, right[3] - 4], 6, SWITCH["card"], SWITCH["line"])

    # Bottom system bar + status line.
    bar_y = body_bottom + 6
    _rounded(draw, [pad, bar_y, width - pad, bar_y + 34], 10, SWITCH["surface"])
    for i, label in enumerate(("刷新", "打开文件夹", "打开本地库", "设置", "监听插拔", "隐藏已备份")):
        bx = pad + 12 + i * 120
        _rounded(draw, [bx, bar_y + 5, bx + 110, bar_y + 29], 6, SWITCH["surface_alt"])
        draw.text((bx + 55, bar_y + 17), label, font=font_small, fill=SWITCH["text"], anchor="mm")
    draw.text((pad, bar_y + 44), "准备好了，插上掌机或打开文件夹就可以开始", font=font_small, fill=SWITCH["muted"], anchor="la")

    out_path = out_dir / "home-preview.png"
    image.save(out_path)
    return out_path


# --- entrypoint -------------------------------------------------------------------


def render_previews(out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    with tempfile.TemporaryDirectory(prefix="vajsave-ui-preview-") as tmp:
        state = build_demo_state(Path(tmp))
        root = None
        app = None
        try:
            root, app = build_offscreen_app(state)
            written.extend(capture_canvases(app, out_dir))
        except Exception as exc:  # noqa: BLE001 - headless/no-display must not abort
            print(f"[ui-preview] 无法构建 Tk 预览（{exc}），改为离线渲染", file=sys.stderr)
        finally:
            try:
                if app is not None:
                    app._stop_background()
            except Exception:  # noqa: BLE001
                pass
            try:
                if root is not None:
                    root.destroy()
            except Exception:  # noqa: BLE001
                pass
        written.append(render_home_preview(out_dir, state))
    return written


def main(argv=None) -> int:
    out_dir = PROJECT_ROOT / "build" / "ui-preview"
    written = render_previews(out_dir)
    for path in written:
        print(f"[ui-preview] {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

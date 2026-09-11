#!/usr/bin/env python3
"""Headless preview renderer for the vaj-save Archive Desk UI.

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

# Demo catalogue: representative rows for a dense archive workspace preview.
DEMO_SAVES = [
    ("switch", "塞尔达传说 王国之泪", "0100F2C0115B6000", "1"),
    ("switch", "宝可梦 朱 / 紫", "0100A3D008C5C000", None),
    ("switch", "斯普拉遁 3", "0100C2500FC20000", None),
    ("switch", "超级马力欧 奥德赛", "0100000000010000", "Auto"),
    ("switch", "集合啦！动物森友会", "01006F8002326000", None),
    ("psp", "Monster Hunter Portable 3rd", "ULJM05800", "1"),
    ("psp", "Persona 3 Portable", "ULUS10512", "1"),
    ("vita", "Hollow Knight", "PCSE00961", None),
    ("3ds", "Fire Emblem Awakening", "CTR-P-AFRE", None),
    ("3ds", "Mario Kart 7", "CTR-AMKP", None),
]


# --- demo state -------------------------------------------------------------------


def _make_demo_cover(root: Path, platform: str, name: str, color: str) -> Optional[Path]:
    """Write a restrained synthetic cover so previews do not show red blocks."""
    try:
        from PIL import Image, ImageDraw

        cover_dir = root / platform
        cover_dir.mkdir(parents=True, exist_ok=True)
        path = cover_dir / f"{name}.png"
        background = mix(color, "#ffffff", 0.72)
        image = Image.new("RGB", (128, 160), background)
        draw = ImageDraw.Draw(image)
        draw.rectangle([0, 0, 128, 34], fill=mix(color, "#ffffff", 0.35))
        draw.rectangle([14, 56, 114, 62], fill=mix(color, "#ffffff", 0.25))
        draw.rectangle([14, 70, 96, 76], fill=mix(color, "#ffffff", 0.25))
        draw.ellipse([82, 92, 112, 122], fill=mix(color, "#ffffff", 0.45))
        draw.ellipse([92, 102, 104, 114], fill=color)
        draw.text((14, 18), platform.upper(), fill="#ffffff", anchor="lm", font=_load_font(10, bold=True))
        draw.text((14, 142), name[:15], fill=mix(color, "#1f2937", 0.6), anchor="lm", font=_load_font(9))
        image.save(path)
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
    root.geometry("1320x780")
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


# --- Archive Desk preview ---------------------------------------------------------


def render_home_preview(out_dir: Path, state: AppState) -> Path:
    """按实际 Tk 布局绘制一张无重叠的档案工作台示意图。"""
    from PIL import Image, ImageDraw

    width, height = 1480, 900
    top_h, bottom_h = 68, 42
    body_top, body_bottom = top_h, height - bottom_h
    image = Image.new("RGB", (width, height), SWITCH["window"])
    draw = ImageDraw.Draw(image)
    font_title = _load_font(21, bold=True)
    font_heading = _load_font(20, bold=True)
    font_body = _load_font(13)
    font_small = _load_font(11)
    font_status = _load_font(11, bold=True)
    ink = SWITCH["ink"]
    muted = SWITCH["muted_strong"]
    selected_fill = SWITCH["selected_soft"]
    border = SWITCH["border_soft"]
    panel_alt = SWITCH["panel_alt"]
    green = SWITCH["status_green"]
    orange = SWITCH["status_orange"]

    # Brand bar.
    draw.rectangle([0, 0, width, top_h], fill=SWITCH["window"])
    mark_x, mark_y = 24, 13
    draw.rectangle([mark_x, mark_y, mark_x + 42, mark_y + 42], fill=ink)
    for offset in (12, 19, 26):
        draw.rectangle([mark_x + 10, mark_y + offset, mark_x + 32, mark_y + offset + 4], fill=SWITCH["window"])
    draw.text((78, 18), "vaj-save", font=font_title, fill=ink, anchor="la")
    draw.text((78, 47), "守护每一段游戏时光", font=font_small, fill=muted, anchor="la")
    stats = state.collection_stats()
    draw.text((width - 218, 34), f"已备份 {stats['games']} 款游戏 · {stats['versions']} 个版本", font=font_small, fill=muted, anchor="rm")
    draw.text((width - 126, 34), "⚙  设置", font=font_small, fill=ink, anchor="mm")
    draw.text((width - 48, 34), "?  帮助", font=font_small, fill=ink, anchor="mm")

    left_w, right_w = 236, 430
    center_left, center_right = left_w, width - right_w
    draw.line([left_w, body_top, left_w, body_bottom], fill=border)
    draw.line([center_right, body_top, center_right, body_bottom], fill=border)

    # Left rail.
    draw.text((24, body_top + 26), "平台", font=font_body, fill=ink, anchor="la")
    counts = state.platform_counts()
    for index, key in enumerate(PLATFORM_ORDER):
        row_y = body_top + 50 + index * 40
        if key == state.selected_platform:
            _rounded(draw, [10, row_y, left_w - 10, row_y + 34], 6, selected_fill)
        color = PLATFORM_COLORS.get(key, SWITCH["accent"])
        draw.ellipse([25, row_y + 12, 35, row_y + 22], fill=color)
        draw.text((48, row_y + 17), PLATFORM_LABELS.get(key, key), font=font_body, fill=ink, anchor="lm")
        count = len(state.all_saves()) if key == "all" else counts.get(key, 0)
        draw.text((left_w - 22, row_y + 17), str(count), font=font_small, fill=muted, anchor="rm")
    device_y = body_top + 50 + len(PLATFORM_ORDER) * 40 + 22
    draw.text((24, device_y), "已连接设备", font=font_body, fill=ink, anchor="la")
    draw.text((left_w - 22, device_y), "⟳", font=font_body, fill=muted, anchor="ra")
    _rounded(draw, [10, device_y + 24, left_w - 10, device_y + 86], 6, selected_fill, border)
    draw.ellipse([26, device_y + 45, 36, device_y + 55], fill=green)
    draw.text((50, device_y + 44), "DEMO 掌机", font=font_body, fill=ink, anchor="lm")
    draw.text((50, device_y + 67), "已连接 · 自动监听", font=font_small, fill=muted, anchor="lm")
    draw.text((24, device_y + 112), "＋  添加设备", font=font_body, fill=SWITCH["accent"], anchor="la")
    library_y = body_bottom - 112
    draw.line([24, library_y - 18, left_w - 24, library_y - 18], fill=border)
    _rounded(draw, [12, library_y, left_w - 12, body_bottom - 16], 6, panel_alt, border)
    draw.text((26, library_y + 18), "本地存档库", font=font_body, fill=ink, anchor="la")
    draw.text((26, library_y + 43), "~/vaj-save/Archive", font=font_small, fill=muted, anchor="la")
    _rounded(draw, [26, library_y + 67, left_w - 26, library_y + 75], 4, SWITCH["line_soft"])
    draw.rectangle([26, library_y + 67, left_w - 85, library_y + 75], fill=SWITCH["accent"])

    # Center archive table.
    center_pad = 22
    draw.text((center_left + center_pad, body_top + 34), "存档档案", font=font_heading, fill=ink, anchor="la")
    draw.text((center_left + center_pad, body_top + 62), f"共 {len(state.visible_saves())} 个可见存档 · 按最近备份时间排序", font=font_small, fill=muted, anchor="la")
    search_x = center_right - 374
    _rounded(draw, [search_x, body_top + 20, center_right - 150, body_top + 54], 6, SWITCH["card"], border)
    draw.text((search_x + 14, body_top + 37), "⌕  搜索游戏名、Title ID 或备注…", font=font_small, fill=muted, anchor="lm")
    _rounded(draw, [center_right - 142, body_top + 20, center_right - 22, body_top + 54], 6, SWITCH["card"], border)
    draw.text((center_right - 82, body_top + 37), "↕  最近备份时间", font=font_small, fill=ink, anchor="mm")
    header_y = body_top + 91
    for label, x in (("游戏", center_left + center_pad), ("平台", center_right - 378), ("Title ID", center_right - 300), ("最近备份", center_right - 192), ("状态", center_right - 45)):
        draw.text((x, header_y), label, font=font_small, fill=muted, anchor="la")

    visible = state.visible_saves()
    row_top, row_right = body_top + 108, center_right - 18
    for index, save in enumerate(visible):
        status = state.save_status(save)
        row = save_row(save, status)
        y1, y2 = row_top + index * ROW_HEIGHT, row_top + (index + 1) * ROW_HEIGHT
        if index == 0:
            _rounded(draw, [center_left + 10, y1 + 2, row_right, y2 - 2], 7, selected_fill)
        cover_x, cover_y = center_left + 22, y1 + 9
        cover_path = resolve_cover(save, state.library_root)
        thumb = load_thumbnail(cover_path, ROW_COVER, ROW_COVER, radius=ROW_COVER_RADIUS) if cover_path else None
        if thumb is not None:
            image.paste(thumb, (cover_x, cover_y), thumb)
        else:
            _rounded(draw, [cover_x, cover_y, cover_x + ROW_COVER, cover_y + ROW_COVER], 7, panel_alt)
        text_x = cover_x + ROW_COVER + 14
        draw.text((text_x, y1 + 27), row["title"][:22], font=font_body, fill=ink, anchor="lm")
        if row["subtitle"]:
            draw.text((text_x, y1 + 49), row["subtitle"][:22], font=font_small, fill=muted, anchor="lm")
        draw.text((center_right - 378, y1 + 35), PLATFORM_LABELS.get(save.platform, save.platform), font=font_small, fill=ink, anchor="la")
        draw.text((center_right - 300, y1 + 35), (save.title_id or "—")[:15], font=font_small, fill=muted, anchor="la")
        draw.text((center_right - 192, y1 + 35), "—", font=font_small, fill=muted, anchor="la")
        status_color = orange if row["status"] == "changed" else green
        draw.ellipse([center_right - 90, y1 + 25, center_right - 80, y1 + 35], fill=status_color)
        draw.text((center_right - 72, y1 + 29), row["status_label"], font=font_status, fill=ink, anchor="lm")
        draw.text((center_right - 72, y1 + 49), f"{len(state.versions_for_entry(save))} 个版本", font=font_small, fill=muted, anchor="lm")
        draw.line([center_left + 18, y2, row_right, y2], fill=border)

    # Right inspector. Every section owns a vertical band, so no item is placed
    # over another item's text or action area.
    right_left = center_right
    draw.text((right_left + 20, body_top + 26), "存档详情", font=font_body, fill=ink, anchor="la")
    selected = visible[0] if visible else None
    selected_name = selected.display_name if selected else "未选择游戏"
    cover_x, cover_y = right_left + 20, body_top + 52
    cover_path = resolve_cover(selected, state.library_root) if selected else None
    thumb = load_thumbnail(cover_path, 108, 144, radius=8) if cover_path else None
    if thumb is not None:
        image.paste(thumb, (cover_x, cover_y), thumb)
    else:
        _rounded(draw, [cover_x, cover_y, cover_x + 108, cover_y + 144], 8, panel_alt, border)
    info_x = cover_x + 122
    draw.text((info_x, cover_y + 15), selected_name[:15], font=_load_font(15, bold=True), fill=ink, anchor="la")
    draw.text((info_x, cover_y + 43), (selected.title_id if selected else "")[:20], font=font_small, fill=muted, anchor="la")
    detail_pairs = (("平台", PLATFORM_LABELS.get(selected.platform, selected.platform) if selected else "—"), ("Title ID", selected.title_id if selected else "—"), ("版本", "尚未备份"), ("存档大小", "—"), ("最近备份", "—"), ("状态", "新"))
    for index, (label, value) in enumerate(detail_pairs):
        col, row = index % 2, index // 2
        x, y = right_left + 20 + col * 190, body_top + 218 + row * 22
        draw.text((x, y), label, font=font_small, fill=muted, anchor="la")
        draw.text((x + 54, y), str(value)[:16], font=font_small, fill=ink, anchor="la")
    draw.text((right_left + 20, body_top + 287), "存档位置", font=font_small, fill=muted, anchor="la")
    draw.text((right_left + 74, body_top + 287), "~/vaj-save/Archive/…", font=font_small, fill=ink, anchor="la")

    primary_y = body_top + 316
    _rounded(draw, [right_left + 20, primary_y, width - 20, primary_y + 40], 6, SWITCH["accent"])
    draw.text(((right_left + width - 20) / 2, primary_y + 20), "备份存档", font=font_body, fill=SWITCH["on_accent"], anchor="mm")
    secondary_y, button_w = primary_y + 51, 119
    for index, label in enumerate(("恢复", "导出 ZIP", "打开位置")):
        x1 = right_left + 20 + index * (button_w + 6)
        _rounded(draw, [x1, secondary_y, x1 + button_w, secondary_y + 34], 6, SWITCH["card"], border)
        draw.text((x1 + button_w / 2, secondary_y + 17), label, font=font_small, fill=ink, anchor="mm")

    versions_top = secondary_y + 60
    draw.text((right_left + 20, versions_top), "版本历史", font=font_heading, fill=ink, anchor="la")
    draw.text((width - 20, versions_top), "5 个版本", font=font_small, fill=muted, anchor="ra")
    table_top, table_bottom = versions_top + 28, versions_top + 177
    _rounded(draw, [right_left + 20, table_top, width - 20, table_bottom], 6, SWITCH["card"], border)
    draw.text((right_left + 34, table_top + 18), "版本", font=font_small, fill=muted, anchor="la")
    draw.text((right_left + 106, table_top + 18), "备份时间", font=font_small, fill=muted, anchor="la")
    for index in range(4):
        vy = table_top + 45 + index * 28
        if index == 0:
            draw.rectangle([right_left + 21, vy - 13, width - 21, vy + 13], fill=selected_fill)
        draw.ellipse([right_left + 34, vy - 5, right_left + 44, vy + 5], outline=SWITCH["accent"] if index == 0 else SWITCH["line_strong"], width=2)
        draw.text((right_left + 58, vy), f"v{4 - index}", font=font_small, fill=ink, anchor="lm")
        draw.text((right_left + 106, vy), f"2024-10-{28 - index * 5:02d} 20:14", font=font_small, fill=muted, anchor="lm")
        draw.text((width - 34, vy), "64 MB", font=font_small, fill=muted, anchor="ra")

    note_y = table_bottom + 22
    draw.text((right_left + 20, note_y), "备注", font=font_body, fill=ink, anchor="la")
    draw.text((width - 20, note_y), "自动保存", font=font_small, fill=muted, anchor="ra")
    _rounded(draw, [right_left + 20, note_y + 22, width - 20, note_y + 66], 6, SWITCH["card"], border)
    draw.text((right_left + 34, note_y + 44), "通关前先保留多个进度。", font=font_small, fill=muted, anchor="lm")

    # Bottom status bar.
    draw.line([0, body_bottom, width, body_bottom], fill=border)
    draw.ellipse([24, body_bottom + 16, 34, body_bottom + 26], fill=green)
    draw.text((46, body_bottom + 21), "1 个设备已连接", font=font_small, fill=ink, anchor="lm")
    draw.text((204, body_bottom + 21), f"已加载 {len(visible)} 个游戏存档", font=font_small, fill=muted, anchor="lm")
    draw.ellipse([width - 192, body_bottom + 16, width - 182, body_bottom + 26], fill=green)
    draw.text((width - 172, body_bottom + 21), "备份完成", font=font_small, fill=ink, anchor="lm")
    draw.text((width - 20, body_bottom + 21), "无待处理任务", font=font_small, fill=muted, anchor="ra")

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

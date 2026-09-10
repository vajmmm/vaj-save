#!/usr/bin/env python3
"""Headless preview renderer for the vaj-save Switch "Basic White" UI.

Builds the real Tk application against a synthetic demo device (no window
interaction, no filesystem scan, no ``mainloop``) and writes preview images to
``build/ui-preview/``.

Two independent renderers are used so the script always leaves something behind:

* ``home-preview.png`` is an **illustrative schematic** drawn with Pillow from the
  same ``ui_theme`` tokens and ``tile_face`` data the live app uses — it is *not*
  a screenshot of the running widgets. It needs no Ghostscript and is stamped
  with a "示意图 · 非真实截图" badge so it can never be mistaken for one.
* ``save-grid.eps`` / ``detail.eps`` are the real captures, taken from the live Tk
  canvases via ``Canvas.postscript`` and converted to PNG through
  Pillow/Ghostscript when a working ``gs`` is available. When Ghostscript is
  missing or broken the ``.eps`` files are kept and the script still exits 0.

Run with::

    python3 scripts/ui_preview.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SRC = PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from vajsave.app_state import PLATFORM_LABELS, PLATFORM_ORDER, AppState  # noqa: E402
from vajsave.app_ui import build_app  # noqa: E402
from vajsave.models import SaveEntry, ScanResult, VolumeInfo  # noqa: E402
from vajsave.ui_theme import PLATFORM_COLORS, SWITCH, grid_columns, mix, tile_face  # noqa: E402

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


def _demo_scan(root: Path) -> ScanResult:
    saves = [
        SaveEntry(
            platform=platform,
            source_id=f"demo_{platform}",
            display_name=name,
            path=str(Path(root) / platform / (title_id or name)),
            title_id=title_id,
            slot=slot,
        )
        for platform, name, title_id, slot in DEMO_SAVES
    ]
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
    for name, canvas in (("save-grid", app.save_list), ("detail", app.detail_canvas)):
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
    """Draw a representative HOME screen from the real theme tokens and tile faces."""
    from PIL import Image, ImageDraw

    width, height = 1180, 740
    pad = 24
    top_h = 64
    image = Image.new("RGB", (width, height), SWITCH["bg"])
    draw = ImageDraw.Draw(image)
    font_title = _load_font(20, bold=True)
    font_body = _load_font(13)
    font_body_bold = _load_font(13, bold=True)
    font_small = _load_font(11)
    font_badge = _load_font(16, bold=True)
    font_mono = _load_font(30, bold=True)

    # Top status bar.
    draw.rectangle([0, 0, width, top_h], fill=SWITCH["surface"])
    draw.ellipse([pad, 14, pad + 36, 50], fill=SWITCH["accent"])
    draw.text((pad + 18, 32), "v", font=font_badge, fill=SWITCH["on_accent"], anchor="mm")
    draw.text((pad + 48, 20), "vaj-save", font=font_title, fill=SWITCH["text"], anchor="la")
    draw.text((pad + 48, 44), "把掌机存档备份下来，按版本管理", font=font_small, fill=SWITCH["muted"], anchor="la")

    # This drawing is a mock, not a rasterization of the live widgets: say so.
    note = "示意图 · 非真实截图"
    note_w = 20 + 11 * len(note)
    _rounded(draw, [width / 2 - note_w / 2, 18, width / 2 + note_w / 2, 46], 14, SWITCH["surface_alt"], SWITCH["line"])
    draw.text((width / 2, 32), note, font=font_small, fill=SWITCH["muted"], anchor="mm")
    stats = state.collection_stats()
    draw.text((width - pad, 22), time.strftime("%H:%M"), font=font_title, fill=SWITCH["text"], anchor="ra")
    draw.text(
        (width - pad, 46),
        f"已备份 {stats['games']} 款游戏 · {stats['versions']} 个版本",
        font=font_small,
        fill=SWITCH["muted"],
        anchor="ra",
    )

    # Search row.
    search_y = top_h + 14
    draw.text((pad, search_y + 8), "搜索", font=font_small, fill=SWITCH["muted"], anchor="la")
    _rounded(draw, [pad + 52, search_y, width - pad, search_y + 30], 15, SWITCH["card"], SWITCH["line"])

    body_top = search_y + 46
    body_bottom = height - 92

    # Left column: platform filters.
    left = [pad, body_top, pad + 220, body_bottom]
    _rounded(draw, left, 16, SWITCH["surface"])
    draw.text((left[0] + 16, body_top + 14), "机种", font=font_small, fill=SWITCH["muted"], anchor="la")
    counts = state.platform_counts()
    for i, key in enumerate(PLATFORM_ORDER):
        row_y = body_top + 44 + i * 34
        selected = key == state.selected_platform
        if selected:
            _rounded(draw, [left[0] + 8, row_y, left[2] - 8, row_y + 30], 10, mix(SWITCH["accent"], SWITCH["card"], 0.84))
        draw.rectangle([left[0] + 14, row_y + 7, left[0] + 17, row_y + 23], fill=PLATFORM_COLORS.get(key, SWITCH["accent"]))
        draw.text((left[0] + 26, row_y + 15), PLATFORM_LABELS.get(key, key), font=font_body, fill=SWITCH["text"], anchor="lm")
        count = len(state.all_saves()) if key == "all" else counts.get(key, 0)
        draw.text((left[2] - 16, row_y + 15), str(count), font=font_small, fill=SWITCH["muted"], anchor="rm")

    # Middle column: white rounded tiles, clipped to the column bounds.
    mid = [pad + 234, body_top, width - pad - 350, body_bottom]
    faces = [tile_face(save, state.save_status(save)) for save in state.visible_saves()]
    tile_w, tile_h, gap = 246, 246, 14
    mid_w, mid_h = mid[2] - mid[0], mid[3] - mid[1]
    tiles_layer = Image.new("RGB", (mid_w, mid_h), SWITCH["bg"])
    layer_draw = ImageDraw.Draw(tiles_layer)
    cols = grid_columns(mid_w, tile_w, gap)
    for i, face in enumerate(faces):
        row, col = divmod(i, cols)
        x1 = col * (tile_w + gap)
        y1 = 40 + row * (tile_h + gap)
        x2, y2 = x1 + tile_w, y1 + tile_h
        # Pastel platform face, big centred monogram, full-width bottom bar.
        _rounded(layer_draw, [x1, y1, x2, y2], 20, face.get("face", SWITCH["card"]))
        layer_draw.text(
            (x1 + tile_w / 2, y1 + tile_h * 0.40),
            face["monogram"],
            font=font_mono,
            fill=face["accent"],
            anchor="mm",
        )
        layer_draw.rectangle([x1, y2 - 4, x2, y2], fill=face["accent"])
        layer_draw.text(
            (x1 + tile_w / 2, y1 + tile_h * 0.62),
            face["title"],
            font=font_body_bold,
            fill=SWITCH["text"],
            anchor="ma",
        )
        pill = face["pill"]
        pill_w = min(tile_w - 30, 18 + 13 * len(pill["label"]))
        px1 = x1 + (tile_w - pill_w) / 2
        _rounded(layer_draw, [px1, y2 - 38, px1 + pill_w, y2 - 16], 11, pill["bg"])
        layer_draw.text((x1 + tile_w / 2, y2 - 27), pill["label"], font=font_small, fill=pill["fg"], anchor="mm")
    image.paste(tiles_layer, (mid[0], mid[1]))
    draw.text((mid[0], body_top + 14), "游戏", font=font_small, fill=SWITCH["muted"], anchor="la")

    # Right column: detail + pinned actions.
    right = [width - pad - 340, body_top, width - pad, body_bottom]
    _rounded(draw, right, 16, SWITCH["surface"])
    draw.text((right[0] + 16, body_top + 14), "详情", font=font_small, fill=SWITCH["muted"], anchor="la")
    selected_name = faces[0]["title"] if faces else "未选择游戏"
    draw.text((right[0] + 16, body_top + 44), selected_name, font=font_title, fill=SWITCH["text"], anchor="la")
    draw.text((right[0] + 16, body_top + 72), "状态: 新", font=font_small, fill=SWITCH["muted"], anchor="la")
    button_y = right[3] - 16 - 8 * 30
    for label, accent in (
        ("备份", True),
        ("备份所选", False),
        ("备份当前列表", False),
        ("恢复这个版本…", False),
        ("导出 ZIP", False),
        ("收藏", False),
        ("在访达中显示", False),
        ("只看收藏", False),
    ):
        fill = SWITCH["accent"] if accent else SWITCH["surface_alt"]
        color = SWITCH["on_accent"] if accent else SWITCH["text"]
        _rounded(draw, [right[0] + 12, button_y, right[2] - 12, button_y + 24], 12, fill)
        draw.text(((right[0] + right[2]) / 2, button_y + 12), label, font=font_small, fill=color, anchor="mm")
        button_y += 30

    # Bottom system bar + status line.
    bar_y = body_bottom + 10
    _rounded(draw, [pad, bar_y, width - pad, bar_y + 38], 14, SWITCH["surface"])
    for i, label in enumerate(("刷新", "打开文件夹", "打开本地库", "设置", "监听插拔", "隐藏已备份")):
        bx = pad + 12 + i * 120
        _rounded(draw, [bx, bar_y + 7, bx + 110, bar_y + 31], 12, SWITCH["surface_alt"])
        draw.text((bx + 55, bar_y + 19), label, font=font_small, fill=SWITCH["text"], anchor="mm")
    draw.text((pad, bar_y + 52), "准备好了，插上掌机或打开文件夹就可以开始", font=font_small, fill=SWITCH["muted"], anchor="la")

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

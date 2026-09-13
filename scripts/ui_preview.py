#!/usr/bin/env python3
"""为 vaj-save 收藏架界面生成离线示意图和真实 Canvas 抓取。

``home-preview.png`` 由 Pillow 使用应用主题 Token 绘制，不依赖显示服务；图片会
明确标注“示意图 · 非真实截图”。若 Tk 与 Ghostscript 可用，脚本还会抓取真实的
``save_list`` Canvas 并生成 EPS/PNG。任何可选能力不可用时，离线示意图仍会生成。
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SRC = PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from vajsave.app_state import PLATFORM_LABELS, PLATFORM_ORDER, AppState  # noqa: E402
from vajsave.identity import unresolved  # noqa: E402
from vajsave.library import SaveBackupStatus, backup_save  # noqa: E402
from vajsave.qt_ui import build_app  # noqa: E402
from vajsave.covers import load_thumbnail, resolve_cover  # noqa: E402
from vajsave.models import SaveEntry, ScanResult, VolumeInfo  # noqa: E402
from vajsave.ui_theme import (  # noqa: E402
    GALLERY_CARD_WIDTH,
    GALLERY_DISPLAY_HEIGHT,
    GALLERY_SHELF_INSET,
    GALLERY_SHELF_SHADOW_HEIGHT,
    GALLERY_SHELF_HEIGHT,
    PLATFORM_COLORS,
    SWITCH,
    darken,
    mix,
)

# Keep preview geometry tied to the same named metrics as the live responsive
# gallery.  The aliases also make it obvious in a generated-artifact review
# that shelves are intentional gallery primitives rather than table rows.
GALLERY_PREVIEW_COLUMNS = 4
SHELF_DEPTH = GALLERY_SHELF_HEIGHT
# Human-readable geometry name shared with design review notes.
grid_columns = GALLERY_PREVIEW_COLUMNS

DEMO_SAVES = [
    ("switch", "塞尔达传说 王国之泪", "0100F2C0115B6000", "1"),
    ("switch", "马力欧卡丁车 8 豪华版", "0100152000022000", None),
    ("switch", "宝可梦 朱 / 紫", "0100A3D008C5C000", None),
    ("switch", "超级马力欧 奥德赛", "0100000000010000", "Auto"),
    ("3ds", "宝可梦 心金", "CTR-P-IPKE", None),
    ("3ds", "马力欧卡丁车 7", "CTR-AMKP", None),
    ("3ds", "新·光神话 帕鲁迪那之镜", "CTR-AKDJ", None),
    ("gba", "宝可梦 绿宝石", "AGB-BPEE", None),
    ("psp", "Monster Hunter Portable 3rd", "ULJM05800", "1"),
    ("vita", "Hollow Knight", "PCSE00961", None),
]

# These small local crops are already part of the visual QA fixture and mirror
# the artwork visible in the supplied reference.  They are used only by the
# preview/demo state; production scans continue to resolve covers through the
# normal artwork service.
_REFERENCE_ARTWORK = (
    "zelda",
    "mk8",
    "pikachu",
    "odyssey",
    "heartgold",
    "mk7",
    "kidicarus",
    "emerald",
)


def _load_font(size: int, bold: bool = False):
    from PIL import ImageFont

    candidates: List[Path] = [
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    ]
    windir = os.environ.get("WINDIR")
    if windir:
        candidates.insert(0, Path(windir) / "Fonts" / ("msyhbd.ttc" if bold else "msyh.ttc"))
    for path in candidates:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size, index=1 if bold else 0)
            except Exception:  # noqa: BLE001 - 继续尝试跨平台候选字体
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _rounded(draw, box, radius: int, fill: str, outline: Optional[str] = None, width: int = 1) -> None:
    try:
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
    except Exception:  # noqa: BLE001 - 兼容较旧 Pillow
        draw.rectangle(box, fill=fill, outline=outline, width=width)


def _make_demo_cover(root: Path, platform: str, name: str, color: str, index: int) -> Optional[Path]:
    """生成预览封面；优先使用本地参考裁切图，缺失时回退为抽象图。"""
    try:
        from PIL import Image, ImageDraw

        # Prefer the reference fixture when it is available locally.  The
        # fallback below keeps the script deterministic on a fresh checkout
        # where build artifacts have not been generated yet.
        if index < len(_REFERENCE_ARTWORK):
            reference = PROJECT_ROOT / "build" / "ui-preview" / "demo_boxarts" / f"{_REFERENCE_ARTWORK[index]}.png"
            if reference.is_file():
                return reference

        cover_dir = root / platform
        cover_dir.mkdir(parents=True, exist_ok=True)
        path = cover_dir / f"{name}.png"
        image = Image.new("RGB", (300, 430), mix(color, SWITCH["window"], 0.78))
        draw = ImageDraw.Draw(image)
        dark = mix(color, SWITCH["ink"], 0.42)
        draw.rectangle([0, 0, 300, 55], fill=color)
        draw.polygon([(0, 280), (300, 160), (300, 430), (0, 430)], fill=dark)
        draw.ellipse([55 + index % 3 * 18, 98, 235, 278], fill=mix(color, SWITCH["window"], 0.48))
        draw.ellipse([92, 135, 198, 241], outline=SWITCH["window"], width=9)
        draw.text((20, 28), platform.upper(), fill=SWITCH["on_accent"], anchor="lm", font=_load_font(15, True))
        draw.text((20, 390), name[:13], fill=SWITCH["on_accent"], anchor="lm", font=_load_font(14, True))
        image.save(path)
        return path
    except Exception:  # noqa: BLE001 - 封面失败时由画廊绘制占位面
        return None


def _demo_scan(root: Path) -> ScanResult:
    saves = []
    for index, (platform, name, title_id, slot) in enumerate(DEMO_SAVES):
        entry = SaveEntry(
            platform=platform,
            source_id=f"demo_{platform}",
            display_name=name,
            path=str(root / platform / title_id),
            title_id=title_id,
            slot=slot,
        )
        cover = _make_demo_cover(
            root,
            platform,
            title_id.replace("/", "_"),
            PLATFORM_COLORS.get(platform, SWITCH["accent"]),
            index,
        )
        if cover:
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


def build_offscreen_app(state: AppState):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    qapp = QApplication.instance() or QApplication([])
    state.start_watch = lambda *args, **kwargs: None
    state.stop_watch = lambda *args, **kwargs: None
    first = state.all_saves()[0] if state.all_saves() else None
    if first is not None:
        source = Path(first.path)
        source.mkdir(parents=True, exist_ok=True)
        payload = source / "data.bin"
        start = datetime(2026, 6, 18, 10, 12)
        for index in range(5):
            payload.write_bytes(bytes([index + 1]) * (64 * 1024))
            backup_save(first, state.library_root, when=start + timedelta(days=index * 19))
        state._backup_statuses[first.path] = SaveBackupStatus(
            status="new", last_backup_at="2026-09-12T14:27:00"
        )
        original_resolver = state.resolve_save_identity
        state.resolve_save_identity = lambda entry: unresolved(reason="preview") if entry.path == first.path else original_resolver(entry)
        state.collection_stats = lambda: {"games": 10, "versions": 42}
    window = build_app(state=state)
    window.show()
    QTest.qWait(80)
    if window.gallery.canvas.entries:
        window.gallery.canvas.selected = {0}
        window.gallery.canvas.active = 0
        window.gallery.canvas.selection_changed.emit(window.gallery.canvas.selected_entries())
        QTest.qWait(220)
    return qapp, window


def _rasterize(eps_path: Path, png_path: Path) -> bool:
    try:
        if "GS_LIB" not in os.environ:
            cellar = Path("/usr/local/Cellar/ghostscript")
            if cellar.is_dir():
                inits = [str(p) for p in cellar.glob("**/Resource/Init") if p.is_dir()]
                inits.sort(key=lambda p: ("10.04" not in p, p))
                if inits:
                    os.environ["GS_LIB"] = ":".join(inits)
        from PIL import Image

        with Image.open(eps_path) as image:
            image.load()
            image.convert("RGB").save(png_path)
        return True
    except Exception as exc:  # noqa: BLE001 - Ghostscript 是可选能力
        print(f"[ui-preview] Ghostscript 不可用，保留 {eps_path.name}（{exc}）", file=sys.stderr)
        return False


def capture_canvases(app, out_dir: Path) -> List[Path]:
    written: List[Path] = []
    window_path = out_dir / "qt-window-selected.png"
    gallery_path = out_dir / "save-gallery.png"
    if app.grab().save(str(window_path)):
        written.append(window_path)
    if app.gallery.viewport().grab().save(str(gallery_path)):
        written.append(gallery_path)
    return written


def _draw_topbar(draw, width: int, top_h: int, stats: dict, fonts: dict) -> None:
    ink, muted = SWITCH["ink"], SWITCH["muted_strong"]
    draw.rectangle([0, 0, width, top_h], fill=SWITCH["surface"])
    draw.line([0, top_h - 1, width, top_h - 1], fill=SWITCH["border_soft"])
    _rounded(draw, [22, 14, 64, 54], 8, SWITCH["selected_soft"])
    for offset in (0, 8, 16):
        draw.rounded_rectangle([34, 25 + offset, 53, 28 + offset], radius=2, fill=ink)
    draw.text((82, 24), "vaj-save", font=fonts["title"], fill=ink, anchor="la")
    draw.text((82, 49), "守护每一段游戏时光", font=fonts["small"], fill=muted, anchor="la")

    _rounded(draw, [410, 14, 770, 54], 9, SWITCH["surface_alt"], SWITCH["border_soft"])
    draw.arc([428, 26, 441, 39], start=35, end=325, fill=muted, width=2)
    draw.line([439, 37, 445, 43], fill=muted, width=2)
    draw.text((458, 34), "搜索游戏名、Title ID 或其他信息…", font=fonts["small"], fill=muted, anchor="lm")
    _rounded(draw, [784, 14, 930, 54], 8, SWITCH["card"], SWITCH["border_soft"])
    draw.line([806, 25, 806, 43], fill=ink, width=2)
    draw.line([802, 29, 806, 25, 810, 29], fill=ink, width=2)
    draw.line([814, 43, 814, 25], fill=ink, width=2)
    draw.line([810, 39, 814, 43, 818, 39], fill=ink, width=2)
    draw.text((871, 34), "最近备份时间", font=fonts["small"], fill=ink, anchor="mm")
    _rounded(draw, [944, 14, 1092, 54], 8, SWITCH["card"], SWITCH["border_soft"])
    draw.rectangle([960, 27, 973, 40], outline=SWITCH["accent"], width=2)
    draw.text((985, 34), "仅显示有更新", font=fonts["small"], fill=ink, anchor="lm")
    draw.line([1128, 27, 1128, 41], fill=ink, width=2)
    draw.line([1123, 31, 1133, 31], fill=ink, width=2)
    draw.line([1125, 37, 1131, 37], fill=ink, width=2)
    draw.text((1142, 34), "设置", font=fonts["small"], fill=ink, anchor="lm")
    draw.text((1212, 34), "?  帮助", font=fonts["small"], fill=ink, anchor="lm")
    draw.text((width - 20, 34), f"已备份 {stats['games']} 款游戏 · {stats['versions']} 个版本", font=fonts["small"], fill=muted, anchor="rm")


def _draw_dock(draw, body_top: int, body_bottom: int, state: AppState, fonts: dict) -> int:
    dock_w = 116
    draw.rectangle([0, body_top, dock_w, body_bottom], fill=SWITCH["panel_alt"])
    draw.line([dock_w, body_top, dock_w, body_bottom], fill=SWITCH["border_soft"])
    counts = state.platform_counts()
    for index, key in enumerate(PLATFORM_ORDER):
        y = body_top + 18 + index * 66
        if key == state.selected_platform:
            _rounded(draw, [10, y, dock_w - 10, y + 58], 9, SWITCH["selected_soft"])
        color = PLATFORM_COLORS.get(key, SWITCH["accent"])
        _rounded(draw, [37, y + 7, 67, y + 29], 6, SWITCH["card"], color, 2)
        if key == "all":
            for dx, dy in ((0, 0), (9, 0), (0, 9), (9, 9)):
                draw.rectangle([43 + dx, y + 12 + dy, 49 + dx, y + 18 + dy], outline=color, width=1)
        else:
            draw.rectangle([44, y + 14, 60, y + 22], fill=color)
        label = PLATFORM_LABELS.get(key, key)
        count = len(state.all_saves()) if key == "all" else counts.get(key, 0)
        draw.text((52, y + 42), label, font=fonts["small"], fill=SWITCH["ink"], anchor="mm")
        if count:
            draw.text((82, y + 15), str(count), font=fonts["tiny"], fill=SWITCH["muted_strong"], anchor="mm")

    divider_y = body_top + 18 + len(PLATFORM_ORDER) * 66
    draw.line([22, divider_y, dock_w - 22, divider_y], fill=SWITCH["border_soft"])
    actions = (("●", "DEMO 掌机"), ("⟳", "刷新设备"), ("＋", "添加设备"), ("▣", "其他设备"), ("⇩", "FTP 拉取"), ("□", "本地存档"))
    for index, (icon, label) in enumerate(actions):
        y = divider_y + 18 + index * 34
        color = SWITCH["status_green"] if index == 0 else SWITCH["muted_strong"]
        draw.text((25, y), icon, font=fonts["small"], fill=color, anchor="lm")
        draw.text((42, y), label, font=fonts["tiny"], fill=SWITCH["ink"], anchor="lm")
    return dock_w


def _case_geometry(platform: str, available_width: int = GALLERY_CARD_WIDTH) -> tuple[int, int]:
    """Mirror ``SaveList._case_size`` for the offline Pillow preview."""
    ratios = {
        "switch": 0.72,
        "psp": 1.06,
        "vita": 0.98,
        "3ds": 0.90,
        "nds": 0.90,
        "gba": 1.18,
    }
    ratio = ratios.get(platform, 0.78)
    max_w = max(1, int(available_width - 18))
    max_h = GALLERY_DISPLAY_HEIGHT - 12
    width = min(max_w, int(max_h * ratio))
    height = min(max_h, int(width / ratio))
    return max(1, width), max(1, height)


def _draw_case(image, draw, save: SaveEntry, x: int, shelf_y: int, selected: bool, state: AppState, fonts: dict, available_width: int = GALLERY_CARD_WIDTH) -> None:
    case_w, case_h = _case_geometry(save.platform, available_width)
    # The cases sit directly on the rear edge of the tray.  The previous
    # preview-only offset left a visible floating gap that the live Canvas did
    # not have and made the shelf read like a detached stripe.
    y = shelf_y - case_h
    shadow = mix(SWITCH["line_strong"], SWITCH["window"], 0.38)
    contact_shadow = mix(darken(SWITCH["shadow_deep"], 0.10), SWITCH["fog_canvas"], 0.28)
    draw.polygon(
        [(x - 5, shelf_y - 3), (x + case_w + 5, shelf_y - 3),
         (x + case_w + 9, shelf_y + 4), (x - 9, shelf_y + 4)],
        fill=contact_shadow,
    )
    if selected:
        _rounded(draw, [x - 8, y - 8, x + case_w + 8, shelf_y + 2], 12, SWITCH["selected_soft"], SWITCH["accent"], 3)
    _rounded(draw, [x + 7, y + 8, x + case_w + 9, shelf_y + 2], 8, shadow)
    _rounded(draw, [x, y, x + case_w, shelf_y - 2], 7, SWITCH["card"], SWITCH["line_strong"], 2)
    draw.line([x + 8, y + 1, x + case_w - 8, y + 1], fill=SWITCH["shelf_highlight"], width=1)
    draw.line([x + case_w - 1, y + 8, x + case_w - 1, shelf_y - 10], fill=SWITCH["shadow_soft"], width=1)
    draw.line([x + 8, shelf_y - 3, x + case_w - 8, shelf_y - 3], fill=SWITCH["shadow_soft"], width=1)
    draw.line([x + 5, y + 4, x + case_w - 5, y + 4], fill=SWITCH["window"], width=2)

    # The platform identity is a 3px pip only; the artwork itself owns all
    # saturated colour (the same rule as the live Canvas gallery).
    pip = PLATFORM_COLORS.get(save.platform, SWITCH["accent"])
    draw.rectangle([x - 3, y + 12, x, y + case_h - 12], fill=pip)
    cover_path = resolve_cover(save, state.library_root)
    art_h = case_h - 12
    thumb = load_thumbnail(cover_path, case_w - 12, art_h, radius=2) if cover_path else None
    if thumb is not None:
        image.paste(thumb, (x + 6, y + 6), thumb)
    else:
        draw.rectangle([x + 6, y + 6, x + case_w - 6, shelf_y - 8], fill=SWITCH["surface_alt"])
        draw.text((x + case_w / 2, y + case_h / 2), save.display_name[:10], font=fonts["body"], fill=SWITCH["ink"], anchor="mm")


def _draw_shelf(draw, left: int, right: int, y: int) -> None:
    depth = max(20, int(SHELF_DEPTH))
    # A shallow recessed tray and a deeper front lip create the same physical
    # proportion as the reference shelf.
    face_depth = min(14, max(10, depth // 3))
    front_left, front_right = left, right
    inset = min(int(GALLERY_SHELF_INSET), max(18, int((front_right - front_left) * 0.12)))
    back_left, back_right = left + inset, right - inset
    # Feather the floor shadow with closely overlapping bands so it reads as a
    # diffuse drop rather than a stack of extra boards.
    shadow_deep = darken(SWITCH["shadow_deep"], 0.18)
    shadow_height = max(24, int(GALLERY_SHELF_SHADOW_HEIGHT))
    shadow_bands = max(12, shadow_height // 2)
    for band in range(shadow_bands):
        progress = band / max(1, shadow_bands - 1)
        start = y + depth + band * 2
        end = start + 3
        shadow_inset = 6 + int(round(progress * 12))
        draw.polygon(
            [(front_left - shadow_inset, start), (front_right + shadow_inset, start),
             (front_right + shadow_inset + 4, end), (front_left - shadow_inset - 4, end)],
            fill=mix(shadow_deep, SWITCH["fog_canvas"], 0.08 + progress * 0.92),
        )
    for band in range(6):
        t0 = band / 6
        t1 = (band + 1) / 6
        y0 = y + face_depth * t0
        y1 = y + face_depth * t1
        left0 = back_left + (front_left - back_left) * t0
        right0 = back_right + (front_right - back_right) * t0
        left1 = back_left + (front_left - back_left) * t1
        right1 = back_right + (front_right - back_right) * t1
        draw.polygon(
            [(left0, y0), (right0, y0), (right1, y1), (left1, y1)],
            fill=mix(SWITCH["shelf_highlight"], SWITCH["fog_canvas"], 0.06 + t0 * 0.18),
        )
    draw.polygon(
        [(back_left, y), (back_right, y), (front_right, y + face_depth), (front_left, y + face_depth)],
        fill=None,
        outline=SWITCH["shelf_highlight"],
    )
    draw.line([back_left, y, back_right, y], fill=SWITCH["shelf_highlight"], width=2)
    draw.line([front_left, y + face_depth, front_right, y + face_depth], fill=SWITCH["line"], width=1)
    lip_height = max(1, depth - face_depth)
    lip_shadow = darken(SWITCH["shadow_deep"], 0.18)
    for band in range(max(6, lip_height)):
        t0 = band / max(1, lip_height)
        t1 = min(1.0, (band + 1) / max(1, lip_height))
        y0 = y + face_depth + lip_height * t0
        y1 = y + face_depth + lip_height * t1
        left0 = front_left - 4 * t0
        right0 = front_right + 4 * t0
        left1 = front_left - 4 * t1
        right1 = front_right + 4 * t1
        draw.polygon(
            [(left0, y0), (right0, y0), (right1, y1), (left1, y1)],
            fill=mix(SWITCH["shelf_face"], lip_shadow, 0.10 + t0 * 0.66),
        )
    draw.polygon(
        [(front_left, y + face_depth), (front_right, y + face_depth),
         (front_right + 4, y + depth), (front_left - 4, y + depth)],
        fill=None,
        outline=SWITCH["shelf_edge"],
    )
    draw.line([front_left, y + face_depth, front_right, y + face_depth], fill=SWITCH["shelf_highlight"], width=1)
    draw.line([front_left - 4, y + depth, front_right + 4, y + depth], fill=SWITCH["shelf_edge"], width=1)


def _draw_action_icon(draw, kind: str, x: float, y: float, color: str, width: int = 2) -> None:
    """Pillow counterpart of ``CanvasButton``'s geometric action icons."""
    if kind == "backup":
        draw.rectangle([x - 8, y - 5, x + 8, y + 1], outline=color, width=width)
        draw.line([x - 8, y - 5, x, y - 9, x + 8, y - 5], fill=color, width=width)
        draw.line([x - 8, y + 1, x, y + 5, x + 8, y + 1], fill=color, width=width)
    elif kind == "restore":
        draw.arc([x - 8, y - 8, x + 8, y + 8], start=45, end=320, fill=color, width=width)
        draw.line([x - 8, y - 1, x - 8, y - 7, x - 2, y - 7], fill=color, width=width)
    elif kind == "export":
        draw.rectangle([x - 7, y - 8, x + 6, y + 8], outline=color, width=width)
        draw.line([x - 3, y - 2, x + 8, y - 2], fill=color, width=width)
        draw.line([x + 4, y - 6, x + 8, y - 2, x + 4, y + 2], fill=color, width=width)
    elif kind == "folder":
        draw.line([x - 9, y - 4, x - 2, y - 4, x + 1, y - 1, x + 9, y - 1], fill=color, width=width)
        draw.line([x - 9, y - 4, x - 9, y + 7, x + 9, y + 7, x + 9, y - 1], fill=color, width=width)
    elif kind == "close":
        draw.line([x - 6, y - 6, x + 6, y + 6], fill=color, width=width)
        draw.line([x + 6, y - 6, x - 6, y + 6], fill=color, width=width)


def _draw_drawer(image, draw, left: int, width: int, body_top: int, body_bottom: int, selected: SaveEntry, state: AppState, fonts: dict) -> None:
    for offset in range(18, 0, -1):
        shade = mix(SWITCH["border_soft"], SWITCH["window"], offset / 22)
        draw.line([left - offset, body_top, left - offset, body_bottom], fill=shade)
    draw.rectangle([left, body_top, width, body_bottom], fill=SWITCH["panel_alt"])
    draw.line([left, body_top, left, body_bottom], fill=SWITCH["border_soft"])
    ink, muted = SWITCH["ink"], SWITCH["muted_strong"]
    draw.text((left + 22, body_top + 30), "存档详情", font=fonts["heading"], fill=ink, anchor="la")
    _draw_action_icon(draw, "close", width - 28, body_top + 30, muted, width=2)

    cover_path = resolve_cover(selected, state.library_root)
    thumb = load_thumbnail(cover_path, 112, 150, radius=7) if cover_path else None
    if thumb is not None:
        image.paste(thumb, (left + 22, body_top + 58), thumb)
    else:
        _rounded(draw, [left + 22, body_top + 58, left + 134, body_top + 208], 7, SWITCH["surface_alt"], SWITCH["border_soft"])
    draw.text((left + 150, body_top + 76), selected.display_name[:17], font=fonts["heading"], fill=ink, anchor="la")
    draw.text((left + 150, body_top + 104), selected.title_id or "—", font=fonts["small"], fill=muted, anchor="la")
    _rounded(draw, [left + 150, body_top + 122, left + 220, body_top + 146], 6, SWITCH["selected_soft"])
    draw.text((left + 185, body_top + 134), PLATFORM_LABELS.get(selected.platform, selected.platform), font=fonts["tiny"], fill=SWITCH["accent"], anchor="mm")

    pairs = (("Title ID", selected.title_id or "—"), ("游戏状态", "新"), ("版本数量", "5 个版本"), ("存档大小", "64 MB"), ("最近备份", "2026-09-12 14:27"), ("存档位置", "~/vaj-save/Archive/…"))
    for index, (label, value) in enumerate(pairs):
        y = body_top + 236 + index * 24
        draw.text((left + 22, y), label, font=fonts["small"], fill=muted, anchor="la")
        draw.text((left + 110, y), value[:27], font=fonts["small"], fill=ink, anchor="la")

    primary_y = body_top + 394
    _rounded(draw, [left + 22, primary_y, width - 22, primary_y + 46], 7, SWITCH["accent"])
    _draw_action_icon(draw, "backup", left + 48, primary_y + 23, SWITCH["on_accent"], width=2)
    draw.text(((left + width) / 2 + 8, primary_y + 23), "备份存档", font=fonts["body_bold"], fill=SWITCH["on_accent"], anchor="mm")
    labels = (("restore", "恢复"), ("export", "导出 ZIP"), ("folder", "打开位置"))
    gap, button_w = 8, (width - left - 44 - 16) // 3
    for index, (icon, label) in enumerate(labels):
        x = left + 22 + index * (button_w + gap)
        _rounded(draw, [x, primary_y + 58, x + button_w, primary_y + 96], 7, SWITCH["card"], SWITCH["border_soft"])
        _draw_action_icon(draw, icon, x + 25, primary_y + 77, ink, width=2)
        draw.text((x + button_w / 2 + 10, primary_y + 77), label, font=fonts["small"], fill=ink, anchor="mm")

    versions_y = primary_y + 124
    draw.text((left + 22, versions_y), "版本历史", font=fonts["heading"], fill=ink, anchor="la")
    draw.text((width - 22, versions_y), "查看全部", font=fonts["small"], fill=SWITCH["accent"], anchor="ra")
    _rounded(draw, [left + 22, versions_y + 22, width - 22, versions_y + 164], 7, SWITCH["card"], SWITCH["border_soft"])
    for index in range(4):
        y = versions_y + 44 + index * 30
        if index == 0:
            draw.rectangle([left + 23, y - 13, width - 23, y + 13], fill=SWITCH["selected_soft"])
        draw.ellipse([left + 36, y - 5, left + 46, y + 5], outline=SWITCH["accent"] if index == 0 else SWITCH["line_strong"], width=2)
        draw.text((left + 58, y), f"v{5 - index}", font=fonts["small"], fill=ink, anchor="lm")
        draw.text((left + 106, y), f"2026-0{9 - index}-12 14:27", font=fonts["small"], fill=muted, anchor="lm")
        draw.text((width - 36, y), "64 MB", font=fonts["small"], fill=muted, anchor="ra")

    note_y = versions_y + 188
    draw.text((left + 22, note_y), "备注", font=fonts["heading"], fill=ink, anchor="la")
    draw.text((width - 22, note_y), "自动保存", font=fonts["small"], fill=muted, anchor="ra")
    _rounded(draw, [left + 22, note_y + 22, width - 22, min(note_y + 78, body_bottom - 18)], 7, SWITCH["card"], SWITCH["border_soft"])
    draw.text((left + 36, note_y + 48), "添加备注…", font=fonts["small"], fill=muted, anchor="lm")


def render_home_preview(out_dir: Path, state: AppState) -> Path:
    """绘制已批准的收藏架布局示意图。"""
    from PIL import Image, ImageDraw

    width, height = 1480, 900
    top_h, bottom_h = 74, 42
    body_top, body_bottom = top_h, height - bottom_h
    image = Image.new("RGB", (width, height), SWITCH["fog_canvas"])
    draw = ImageDraw.Draw(image)
    fonts = {
        "title": _load_font(21, True),
        "heading": _load_font(16, True),
        "body": _load_font(13),
        "body_bold": _load_font(13, True),
        "small": _load_font(11),
        "tiny": _load_font(9),
        "tiny_bold": _load_font(9, True),
    }
    visible = state.visible_saves()
    # The offline fixture has no persisted library catalog, but the screenshot
    # still needs one coherent set of demo counts across the top and bottom
    # status surfaces.  Keep these numbers presentation-only; the live app
    # continues to read collection_stats() from the real catalog.
    preview_stats = {
        "games": len(visible),
        "versions": 42 if visible else 0,
    }
    _draw_topbar(draw, width, top_h, preview_stats, fonts)
    dock_w = _draw_dock(draw, body_top, body_bottom, state, fonts)

    drawer_left = width - 430
    gallery_left, gallery_right = dock_w, drawer_left
    draw.rectangle([gallery_left, body_top, gallery_right, body_bottom], fill=SWITCH["fog_canvas"])
    # Leave a quiet perimeter around the cases while letting the shelf itself
    # run almost to the drawer edge.  This prevents the first/last package
    # from looking glued to the shelf ends.
    shelf_left, shelf_right = gallery_left + 12, gallery_right - 12
    card_left, card_right = gallery_left + 48, gallery_right - 32
    columns = grid_columns
    cell_w = (card_right - card_left) // columns
    shelf_pitch = GALLERY_DISPLAY_HEIGHT + 10 + SHELF_DEPTH + 20
    shelf_levels = (body_top + 394, body_top + 394 + shelf_pitch)
    for row, shelf_y in enumerate(shelf_levels):
        _draw_shelf(draw, shelf_left, shelf_right, shelf_y)
        for col in range(columns):
            index = row * columns + col
            if index >= len(visible):
                break
            case_w, _ = _case_geometry(visible[index].platform, cell_w)
            x = card_left + col * cell_w + (cell_w - case_w) // 2
            _draw_case(image, draw, visible[index], x, shelf_y, index == 0, state, fonts, cell_w)

    if visible:
        _draw_drawer(image, draw, drawer_left, width, body_top, body_bottom, visible[0], state, fonts)

    draw.line([0, body_bottom, width, body_bottom], fill=SWITCH["border_soft"])
    draw.rectangle([0, body_bottom + 1, width, height], fill=SWITCH["surface"])
    draw.ellipse([22, body_bottom + 15, 32, body_bottom + 25], fill=SWITCH["status_green"])
    draw.text((44, body_bottom + 20), "DEMO 掌机", font=fonts["small"], fill=SWITCH["ink"], anchor="lm")
    draw.text((132, body_bottom + 20), "192.168.1.193", font=fonts["small"], fill=SWITCH["muted_strong"], anchor="lm")
    draw.text(
        (250, body_bottom + 20),
        f"已加载 {preview_stats['games']} 款游戏 · {preview_stats['versions']} 个版本",
        font=fonts["small"],
        fill=SWITCH["muted_strong"],
        anchor="lm",
    )
    draw.text((452, body_bottom + 20), "最后扫描：2026-09-13 10:24", font=fonts["small"], fill=SWITCH["muted_strong"], anchor="lm")
    draw.ellipse([width - 286, body_bottom + 15, width - 276, body_bottom + 25], fill=SWITCH["status_green"])
    draw.text((width - 266, body_bottom + 20), "监听已开启", font=fonts["small"], fill=SWITCH["ink"], anchor="lm")
    _rounded(draw, [gallery_left + 18, body_top + 12, gallery_left + 142, body_top + 38], 7, SWITCH["card"], SWITCH["border_soft"])
    draw.text((gallery_left + 80, body_top + 25), "示意图 · 非真实截图", font=fonts["tiny"], fill=SWITCH["muted_strong"], anchor="mm")
    out_path = out_dir / "home-preview.png"
    image.save(out_path)
    return out_path


def render_previews(out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    with tempfile.TemporaryDirectory(prefix="vajsave-ui-preview-") as tmp:
        state = build_demo_state(Path(tmp))
        qapp = None
        app = None
        try:
            qapp, app = build_offscreen_app(state)
            written.extend(capture_canvases(app, out_dir))
        except Exception as exc:  # noqa: BLE001 - 服务器无显示时使用离线渲染
            print(f"[ui-preview] 无法构建 Qt 预览（{exc}），改为离线渲染", file=sys.stderr)
        finally:
            try:
                if app is not None:
                    app.close()
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

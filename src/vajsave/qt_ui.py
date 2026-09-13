"""vaj-save 的 PySide6 桌面界面。

界面层只调用 :class:`vajsave.app_state.AppState` 的公开业务接口。存档扫描、备份、
恢复和元数据格式仍由原有 Python 模块负责，Qt 仅承担显示与交互。
"""

from __future__ import annotations

import os
import sys
from importlib import resources
from pathlib import Path
from typing import Iterable, Optional

# QtAwesome 通过 QtPy 选择绑定；显式固定为 PySide6，避免安装了 PyQt5 的
# Python 环境生成不兼容的 QIcon 对象。
os.environ["QT_API"] = "pyside6"

import qtawesome as qta
from PySide6.QtCore import QByteArray, QEasingCurve, QObject, QPoint, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QIcon, QKeyEvent, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtSvg import QSvgRenderer

from .app_state import PLATFORM_LABELS, PLATFORM_ORDER, AppState
from .artwork import LLM_PROTOCOLS, ArtworkLoader, default_base_url, default_model
from .identity import STATUS_AMBIGUOUS, STATUS_PARTIAL, STATUS_RESOLVED
from .library import Snapshot, load_keep_last
from .models import SaveEntry, VolumeInfo
from .rom_formats import supported_extensions
from .ui_theme import PLATFORM_COLORS, SWITCH, darken, mix


FONT_FAMILY = "PingFang SC" if sys.platform == "darwin" else ("Microsoft YaHei" if sys.platform == "win32" else "Noto Sans CJK SC")
DRAWER_WIDTH = 442
TOPBAR_HEIGHT = 66
BOTTOMBAR_HEIGHT = 42
DOCK_WIDTH = 104
TOPBAR_TOP = mix(SWITCH["fog_top"], SWITCH["card"], 0.72)
CONTROL_WHITE = mix(SWITCH["fog_panel"], SWITCH["card"], 0.76)
SHELF_FACE_TOP = mix(SWITCH["shelf_face"], SWITCH["card"], 0.62)
SHELF_FACE_BOTTOM = mix(SWITCH["shelf_face"], SWITCH["shadow_deep"], 0.24)
WARNING_SURFACE = mix(SWITCH["card"], SWITCH["warning"], 0.13)
WARNING_BORDER = mix(SWITCH["card"], SWITCH["warning"], 0.28)
WARNING_TEXT = darken(SWITCH["warning"], 0.30)


class _QtCallbackBridge(QObject):
    """把后台线程完成回调安全地投递回 Qt 主线程。"""

    dispatch = Signal(object)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.dispatch.connect(self._run)

    @Slot(object)
    def _run(self, callback) -> None:
        callback()


def _icon(name: str, color: str = SWITCH["ink"], scale: float = 1.0):
    return qta.icon(name, color=color, scale_factor=scale)


_PLATFORM_LOGOS = {
    "psp": "platform-psp.svg",
    "vita": "platform-vita.svg",
    "switch": "platform-switch.svg",
    "3ds": "platform-3ds.svg",
    "nds": "platform-nds.svg",
    "gba": "platform-gba.svg",
}
_PLATFORM_ICON_CACHE: dict[str, QIcon] = {}
_ICON_MASTER_DPR = 4.0


def _render_platform_logo(platform: str, svg: bytes, dpr: float) -> QPixmap:
    """以物理像素渲染一档平台标识，同时保留逻辑尺寸。"""
    renderer = QSvgRenderer(QByteArray(svg))
    logical_width, logical_height = 68.0, 28.0
    canvas = QPixmap(round(logical_width * dpr), round(logical_height * dpr))
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(Qt.GlobalColor.transparent)
    bounds = renderer.viewBoxF()
    scale = min(64.0 / bounds.width(), 24.0 / bounds.height())
    target = QRectF(
        (logical_width - bounds.width() * scale) / 2.0,
        (logical_height - bounds.height() * scale) / 2.0,
        bounds.width() * scale,
        bounds.height() * scale,
    )
    painter = QPainter(canvas)
    renderer.render(painter, target)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(QRectF(0, 0, logical_width, logical_height), QColor(SWITCH["ink"]))
    painter.end()
    return canvas


def _platform_icon(platform: str) -> QIcon:
    """按原始宽高比渲染官方平台标识，并统一为 Dock 的中性色。"""
    if platform in _PLATFORM_ICON_CACHE:
        return _PLATFORM_ICON_CACHE[platform]
    if platform == "all" or platform not in _PLATFORM_LOGOS:
        icon = _icon("fa6s.table-cells-large", SWITCH["muted_strong"], 0.88)
        _PLATFORM_ICON_CACHE[platform] = icon
        return icon

    svg = resources.files("vajsave.data").joinpath(_PLATFORM_LOGOS[platform]).read_bytes()
    # 只缓存一份 4× 物理像素母版。Qt 在 100%～400% 缩放下只会从它缩小，
    # 不会再把低 DPI 档位放大；这也覆盖 Windows 常见的 125%/150%/175%。
    icon = QIcon(_render_platform_logo(platform, svg, _ICON_MASTER_DPR))
    _PLATFORM_ICON_CACHE[platform] = icon
    return icon


def _cover_source_rect(pixmap: QPixmap, target: QRectF) -> QRectF:
    """返回居中 cover-crop 的原图区域，避免先缩小再由高 DPI 放大。"""
    source_width = float(pixmap.width())
    source_height = float(pixmap.height())
    source_ratio = source_width / source_height
    target_ratio = target.width() / target.height()
    if source_ratio > target_ratio:
        crop_width = source_height * target_ratio
        return QRectF((source_width - crop_width) / 2.0, 0, crop_width, source_height)
    crop_height = source_width / target_ratio
    return QRectF(0, (source_height - crop_height) / 2.0, source_width, crop_height)


def _scaled_pixmap_for_dpr(
    pixmap: QPixmap,
    logical_size: QSize,
    dpr: float,
    aspect_mode: Qt.AspectRatioMode = Qt.AspectRatioMode.KeepAspectRatio,
) -> QPixmap:
    """为固定尺寸控件生成与当前屏幕 DPR 匹配的位图。"""
    physical_size = QSize(round(logical_size.width() * dpr), round(logical_size.height() * dpr))
    scaled = pixmap.scaled(physical_size, aspect_mode, Qt.TransformationMode.SmoothTransformation)
    scaled.setDevicePixelRatio(dpr)
    return scaled


def _fmt_time(value: Optional[str]) -> str:
    return str(value or "—").replace("T", " ")[:16]


def _short_path(value: object, length: int = 45) -> str:
    text = str(value or "—")
    if len(text) <= length:
        return text
    return "…" + text[-(length - 1) :]


def _open_path(path: Path) -> tuple[bool, str]:
    import subprocess

    if not path.exists():
        return False, f"路径不存在：{path}"
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=True)
        elif sys.platform == "win32":
            subprocess.run(["explorer", f"/select,{path}"], check=True)
        else:
            subprocess.run(["xdg-open", str(path if path.is_dir() else path.parent)], check=True)
        return True, f"已打开：{path}"
    except Exception as exc:  # noqa: BLE001
        return False, f"打开失败：{exc}"


def _button(text: str, icon_name: Optional[str] = None, *, primary: bool = False, checkable: bool = False) -> QPushButton:
    button = QPushButton(text)
    if icon_name:
        button.setIcon(_icon(icon_name, SWITCH["on_accent"] if primary else SWITCH["ink"], 0.82))
        button.setIconSize(QSize(18, 18))
    button.setProperty("primary", primary)
    button.setCheckable(checkable)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


class PlatformButton(QToolButton):
    """Dock 中带平台色图标和名称的垂直按钮。"""

    def __init__(self, platform: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.platform = platform
        self.setIcon(_platform_icon(platform))
        self.setIconSize(QSize(68, 28))
        self.setText(PLATFORM_LABELS.get(platform, platform))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(62)
        self.setProperty("platform", True)


class PlatformDock(QFrame):
    platform_selected = Signal(str)
    refresh_requested = Signal()
    add_requested = Signal()
    devices_requested = Signal()
    ftp_requested = Signal()
    library_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("platformDock")
        self.setFixedWidth(DOCK_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 10, 9, 9)
        layout.setSpacing(2)
        self.buttons: dict[str, PlatformButton] = {}
        for key in PLATFORM_ORDER:
            button = PlatformButton(key)
            button.clicked.connect(lambda _checked=False, platform=key: self.platform_selected.emit(platform))
            layout.addWidget(button)
            self.buttons[key] = button
        self.buttons["all"].setChecked(True)
        layout.addStretch(1)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("dockDivider")
        layout.addWidget(line)
        self.device_dot = QLabel("●")
        self.device_dot.setObjectName("deviceDot")
        self.device_label = QLabel("未连接")
        self.device_label.setObjectName("deviceName")
        row = QHBoxLayout()
        row.setContentsMargins(7, 4, 0, 4)
        row.setSpacing(5)
        row.addWidget(self.device_dot)
        row.addWidget(self.device_label, 1)
        layout.addLayout(row)
        actions = (
            ("刷新设备", "fa6s.arrows-rotate", self.refresh_requested),
            ("添加设备", "fa6s.plus", self.add_requested),
            ("其他设备", "fa6s.display", self.devices_requested),
            ("FTP 拉取", "fa6s.cloud-arrow-down", self.ftp_requested),
            ("本地存档", "fa6s.folder", self.library_requested),
        )
        for text, icon_name, signal in actions:
            button = QToolButton()
            button.setText(text)
            button.setIcon(_icon(icon_name, SWITCH["muted_strong"], 0.74))
            button.setIconSize(QSize(15, 15))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty("dockAction", True)
            button.clicked.connect(signal.emit)
            layout.addWidget(button)

    def set_current(self, platform: str) -> None:
        if platform in self.buttons:
            self.buttons[platform].setChecked(True)

    def set_device(self, text: str, connected: bool) -> None:
        self.device_label.setText(text)
        self.device_label.setToolTip(text)
        self.device_dot.setProperty("connected", connected)
        self.device_dot.style().unpolish(self.device_dot)
        self.device_dot.style().polish(self.device_dot)


class GalleryCanvas(QWidget):
    selection_changed = Signal(object)
    activated = Signal(object)

    PAD_X = 30
    PAD_TOP = 44
    CELL_W = 196
    CELL_GAP = 18
    ROW_H = 320
    CASE_H = 286
    SHELF_Y = 322
    AFTER_SECOND_GAP = 78

    def __init__(self, state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state
        self.entries: list[SaveEntry] = []
        self.selected: set[int] = set()
        self.active = -1
        self.anchor = -1
        self.hovered = -1
        self.columns = 1
        self.reserved_right = 0
        self._pixmaps: dict[str, QPixmap] = {}
        self._cover_paths: dict[str, str] = {}
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_entries(self, entries: Iterable[SaveEntry]) -> None:
        current_paths = {self.entries[i].path for i in self.selected if 0 <= i < len(self.entries)}
        self.entries = list(entries)
        self.selected = {i for i, item in enumerate(self.entries) if item.path in current_paths}
        self.active = min(self.active, len(self.entries) - 1)
        self._update_geometry()
        self.update()

    def set_cover_path(self, entry_path: str, cover_path: Optional[str]) -> None:
        """替换一个条目的封面路径，并让下一帧重新读取高清资源。"""
        path = str(cover_path or "")
        previous = self._cover_paths.get(entry_path, "")
        if previous == path:
            return
        self._cover_paths[entry_path] = path
        self.update()

    def set_reserved_right(self, width: int) -> None:
        self.reserved_right = max(0, width)
        self._update_geometry()
        self.update()

    def selected_entries(self) -> list[SaveEntry]:
        return [self.entries[i] for i in sorted(self.selected) if 0 <= i < len(self.entries)]

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_geometry()

    def _usable_width(self) -> int:
        return max(self.CELL_W, self.width() - self.reserved_right - self.PAD_X * 2)

    def _update_geometry(self) -> None:
        usable = self._usable_width()
        self.columns = max(1, min(5, (usable + self.CELL_GAP) // (self.CELL_W + self.CELL_GAP)))
        rows = max(1, (len(self.entries) + self.columns - 1) // self.columns)
        overflow_gap = max(0, rows - 2) * self.AFTER_SECOND_GAP
        self.setMinimumHeight(self.PAD_TOP + rows * self.ROW_H + overflow_gap + 34)

    def _cell_rect(self, index: int) -> QRectF:
        usable = self._usable_width()
        grid_width = self.columns * self.CELL_W + (self.columns - 1) * self.CELL_GAP
        start = self.PAD_X + max(0, (usable - grid_width) / 2)
        col, row = index % self.columns, index // self.columns
        overflow_gap = max(0, row - 1) * self.AFTER_SECOND_GAP
        return QRectF(start + col * (self.CELL_W + self.CELL_GAP), self.PAD_TOP + row * self.ROW_H + overflow_gap, self.CELL_W, self.ROW_H)

    def _case_size(self, entry: SaveEntry) -> tuple[int, int]:
        ratio = {"switch": 0.70, "psp": 0.74, "vita": 0.74, "3ds": 0.90, "nds": 0.90, "gba": 1.12}.get(entry.platform, 0.78)
        height = self.CASE_H
        width = int(height * ratio)
        if width > self.CELL_W - 12:
            width = self.CELL_W - 12
            height = int(width / ratio)
        return width, height

    def _case_rect(self, index: int) -> QRectF:
        cell = self._cell_rect(index)
        width, height = self._case_size(self.entries[index])
        bottom = cell.top() + self.SHELF_Y - 2
        lift = 3 if index == self.hovered else 0
        return QRectF(cell.center().x() - width / 2, bottom - height - lift, width, height)

    def _load_pixmap(self, entry: SaveEntry) -> Optional[QPixmap]:
        path = self._cover_paths.get(entry.path)
        if path is None:
            resolution = self.state.resolve_save_cover(entry)
            path = str(resolution.path or "")
            self._cover_paths[entry.path] = path
        if not path:
            return None
        cached = self._pixmaps.get(path)
        if cached is not None:
            return cached
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return None
        if len(self._pixmaps) >= 256:
            self._pixmaps.clear()
        self._pixmaps[path] = pixmap
        return pixmap

    @staticmethod
    def _rounded_path(rect: QRectF, radius: float) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        return path

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(event.rect(), QColor(SWITCH["fog_canvas"]))
        if not self.entries:
            painter.setPen(QColor(SWITCH["muted_strong"]))
            painter.setFont(QFont(FONT_FAMILY, 13))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "连接掌机或打开本地存档库")
            return
        row_count = (len(self.entries) + self.columns - 1) // self.columns
        shelf_left = 12
        shelf_right = max(shelf_left + 300, self.width() - self.reserved_right - 12)
        for row in range(row_count):
            y = self.PAD_TOP + row * self.ROW_H + max(0, row - 1) * self.AFTER_SECOND_GAP + self.SHELF_Y
            self._paint_shelf(painter, shelf_left, shelf_right, y)
        for index, entry in enumerate(self.entries):
            self._paint_case(painter, index, entry)

    def _paint_shelf(self, painter: QPainter, left: float, right: float, y: float) -> None:
        shadow = QLinearGradient(0, y + 28, 0, y + 78)
        shadow.setColorAt(0, QColor(92, 103, 117, 98))
        shadow.setColorAt(0.34, QColor(118, 128, 140, 52))
        shadow.setColorAt(1, QColor(196, 202, 210, 0))
        painter.fillRect(QRectF(left + 12, y + 24, right - left - 24, 58), shadow)
        top = QPainterPath()
        top.moveTo(left + 32, y - 17)
        top.lineTo(right - 32, y - 17)
        top.lineTo(right, y + 3)
        top.lineTo(left, y + 3)
        top.closeSubpath()
        top_gradient = QLinearGradient(0, y - 17, 0, y + 4)
        top_gradient.setColorAt(0, QColor(SWITCH["shelf_highlight"]))
        top_gradient.setColorAt(1, QColor(SWITCH["shelf_face"]))
        painter.fillPath(top, top_gradient)
        painter.setPen(QPen(QColor(SWITCH["shelf_edge"]), 1))
        painter.drawPath(top)
        face = QRectF(left, y + 3, right - left, 23)
        face_gradient = QLinearGradient(0, y + 3, 0, y + 26)
        face_gradient.setColorAt(0, QColor(SHELF_FACE_TOP))
        face_gradient.setColorAt(0.38, QColor(SWITCH["shelf_face"]))
        face_gradient.setColorAt(1, QColor(SHELF_FACE_BOTTOM))
        painter.fillRect(face, face_gradient)
        painter.setPen(QColor(SWITCH["shelf_edge"]))
        painter.drawLine(int(left), int(y + 26), int(right), int(y + 26))

    def _paint_case(self, painter: QPainter, index: int, entry: SaveEntry) -> None:
        rect = self._case_rect(index)
        selected = index in self.selected
        shadow_rect = rect.translated(6, 9)
        shadow = QLinearGradient(shadow_rect.left(), 0, shadow_rect.right(), 0)
        shadow.setColorAt(0, QColor(65, 75, 86, 25))
        shadow.setColorAt(1, QColor(45, 54, 66, 90))
        painter.fillPath(self._rounded_path(shadow_rect, 6), shadow)
        if selected:
            for spread, alpha in ((8, 32), (5, 58), (2, 115)):
                glow_rect = rect.adjusted(-spread, -spread, spread, spread)
                painter.setPen(QPen(QColor(10, 132, 255, alpha), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(glow_rect, 8, 8)
        case_fill = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
        case_fill.setColorAt(0, QColor(SWITCH["card"]))
        case_fill.setColorAt(1, QColor(mix(SWITCH["card"], SWITCH["shadow_soft"], 0.52)))
        painter.setPen(QPen(QColor(SWITCH["accent"] if selected else mix(SWITCH["line"], SWITCH["shadow_deep"], 0.32)), 2 if selected else 1))
        painter.setBrush(case_fill)
        painter.drawRoundedRect(rect, 6, 6)
        cover_rect = rect.adjusted(6, 6, -6, -7)
        pixmap = self._load_pixmap(entry)
        if pixmap is not None:
            painter.save()
            painter.setClipPath(self._rounded_path(cover_rect, 3))
            painter.drawPixmap(cover_rect, pixmap, _cover_source_rect(pixmap, cover_rect))
            painter.restore()
        else:
            painter.fillPath(self._rounded_path(cover_rect, 3), QColor(SWITCH["panel_alt"]))
            painter.setPen(QColor(SWITCH["muted_strong"]))
            painter.setFont(QFont(FONT_FAMILY, 11, QFont.Weight.DemiBold))
            painter.drawText(cover_rect.adjusted(12, 12, -12, -12), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, entry.display_name or "无封面")
        pip = QColor(PLATFORM_COLORS.get(entry.platform, SWITCH["accent"]))
        painter.fillRect(QRectF(rect.left() + 2, rect.top() + 10, 3, rect.height() - 20), pip)
        painter.setPen(QPen(QColor(255, 255, 255, 180), 1))
        painter.drawLine(int(rect.left() + 8), int(rect.top() + 4), int(rect.right() - 8), int(rect.top() + 4))
        contact = QLinearGradient(0, rect.bottom(), 0, rect.bottom() + 13)
        contact.setColorAt(0, QColor(52, 61, 72, 92))
        contact.setColorAt(1, QColor(52, 61, 72, 0))
        painter.fillRect(QRectF(rect.left() - 4, rect.bottom(), rect.width() + 8, 13), contact)

    def _index_at(self, point: QPoint) -> int:
        for index in range(len(self.entries)):
            cell = self._cell_rect(index)
            if cell.adjusted(0, 0, 0, -58).contains(point):
                return index
        return -1

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = self._index_at(event.position().toPoint())
        if index != self.hovered:
            self.hovered = index
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hovered = -1
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        index = self._index_at(event.position().toPoint())
        if index < 0:
            return
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ShiftModifier and self.anchor >= 0:
            lo, hi = sorted((self.anchor, index))
            self.selected = set(range(lo, hi + 1))
        elif mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            if index in self.selected:
                self.selected.remove(index)
            else:
                self.selected.add(index)
            self.anchor = index
        else:
            self.selected = {index}
            self.anchor = index
        self.active = index
        self.update()
        self.selection_changed.emit(self.selected_entries())

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        index = self._index_at(event.position().toPoint())
        if index >= 0:
            self.activated.emit(self.entries[index])

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self.entries:
            return
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.selected = set(range(len(self.entries)))
            self.selection_changed.emit(self.selected_entries())
            self.update()
            return
        delta = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_Up: -self.columns, Qt.Key.Key_Down: self.columns}.get(event.key())
        if delta is not None:
            current = self.active if self.active >= 0 else 0
            self.active = max(0, min(len(self.entries) - 1, current + delta))
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and self.anchor >= 0:
                lo, hi = sorted((self.anchor, self.active))
                self.selected = set(range(lo, hi + 1))
            else:
                self.selected = {self.active}
                self.anchor = self.active
            self.selection_changed.emit(self.selected_entries())
            self.update()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.active >= 0:
            self.activated.emit(self.entries[self.active])
            return
        super().keyPressEvent(event)


class GalleryView(QScrollArea):
    selection_changed = Signal(object)
    activated = Signal(object)

    def __init__(self, state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("galleryView")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.canvas = GalleryCanvas(state)
        self.setWidget(self.canvas)
        self.canvas.selection_changed.connect(self.selection_changed)
        self.canvas.activated.connect(self.activated)

    def set_entries(self, entries: Iterable[SaveEntry]) -> None:
        self.canvas.set_entries(entries)

    def selected_entries(self) -> list[SaveEntry]:
        return self.canvas.selected_entries()

    def set_reserved_right(self, width: int) -> None:
        self.canvas.set_reserved_right(width)


class DetailDrawer(QFrame):
    close_requested = Signal()
    backup_requested = Signal()
    restore_requested = Signal()
    export_requested = Signal()
    location_requested = Signal()
    bind_requested = Signal()
    version_changed = Signal(int)
    note_committed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("detailDrawer")
        self.setFixedWidth(DRAWER_WIDTH)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setOffset(-9, 0)
        shadow.setColor(QColor(72, 86, 103, 85))
        self.setGraphicsEffect(shadow)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)
        header = QHBoxLayout()
        title = QLabel("存档详情")
        title.setObjectName("drawerTitle")
        close = QToolButton()
        close.setObjectName("drawerClose")
        close.setIcon(_icon("fa6s.xmark", SWITCH["muted_strong"], 0.9))
        close.setIconSize(QSize(22, 22))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.close_requested)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(close)
        root.addLayout(header)
        hero = QHBoxLayout()
        hero.setSpacing(16)
        self.cover = QLabel()
        self.cover.setObjectName("detailCover")
        self.cover.setFixedSize(116, 196)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setScaledContents(False)
        hero.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)
        identity = QVBoxLayout()
        identity.setSpacing(2)
        self.name = QLabel("未选择游戏")
        self.name.setObjectName("detailName")
        self.name.setWordWrap(True)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("detailSubtitle")
        self.subtitle.setWordWrap(True)
        self.platform = QLabel("Switch")
        self.platform.setObjectName("platformChip")
        self.platform.setFixedHeight(24)
        identity.addWidget(self.name)
        identity.addWidget(self.subtitle)
        identity.addWidget(self.platform, 0, Qt.AlignmentFlag.AlignLeft)
        identity.addSpacing(7)
        self.fields: dict[str, QLabel] = {}
        field_grid = QGridLayout()
        field_grid.setHorizontalSpacing(8)
        field_grid.setVerticalSpacing(5)
        for row, (label, key) in enumerate((
            ("Title ID", "title_id"), ("游戏状态", "status"), ("版本数量", "versions"),
            ("存档大小", "size"), ("最近备份", "last_backup"), ("存档位置", "path"),
        )):
            key_label = QLabel(label)
            key_label.setObjectName("fieldKey")
            value = QLabel("—")
            value.setObjectName("fieldValue")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            field_grid.addWidget(key_label, row, 0)
            field_grid.addWidget(value, row, 1)
            self.fields[key] = value
        field_grid.setColumnStretch(1, 1)
        identity.addLayout(field_grid)
        identity.addStretch(1)
        hero.addLayout(identity, 1)
        root.addLayout(hero)
        root.addSpacing(8)
        self.primary = _button("备份存档", "fa6s.database", primary=True)
        self.primary.setFixedHeight(48)
        self.primary.clicked.connect(self.backup_requested)
        root.addWidget(self.primary)
        secondary = QHBoxLayout()
        secondary.setSpacing(10)
        self.restore = _button("恢复", "fa6s.arrow-rotate-left")
        self.export = _button("导出 ZIP", "fa6s.file-arrow-down")
        self.location = _button("打开位置", "fa6s.folder")
        for button in (self.restore, self.export, self.location):
            button.setFixedHeight(40)
            secondary.addWidget(button, 1)
        self.restore.clicked.connect(self.restore_requested)
        self.export.clicked.connect(self.export_requested)
        self.location.clicked.connect(self.location_requested)
        root.addLayout(secondary)
        self.warning = QFrame()
        self.warning.setObjectName("romWarning")
        warning_row = QHBoxLayout(self.warning)
        warning_row.setContentsMargins(14, 10, 12, 10)
        warning_icon = QLabel()
        warning_icon.setPixmap(_icon("fa6s.triangle-exclamation", WARNING_TEXT).pixmap(24, 24))
        warning_text = QVBoxLayout()
        self.warning_title = QLabel("未绑定 ROM")
        self.warning_title.setObjectName("warningTitle")
        self.warning_hint = QLabel("建议绑定对应的游戏 ROM，便于识别游戏版本。")
        self.warning_hint.setObjectName("warningHint")
        self.warning_hint.setWordWrap(True)
        warning_text.addWidget(self.warning_title)
        warning_text.addWidget(self.warning_hint)
        bind = _button("去绑定")
        bind.clicked.connect(self.bind_requested)
        warning_row.addWidget(warning_icon)
        warning_row.addLayout(warning_text, 1)
        warning_row.addWidget(bind)
        root.addWidget(self.warning)
        versions_head = QHBoxLayout()
        self.version_title = QLabel("版本历史")
        self.version_title.setObjectName("sectionTitle")
        self.version_count = QLabel("0 个版本")
        self.version_count.setObjectName("sectionMuted")
        view_all = QLabel("查看全部")
        view_all.setObjectName("linkLabel")
        versions_head.addWidget(self.version_title)
        versions_head.addWidget(self.version_count)
        versions_head.addStretch(1)
        versions_head.addWidget(view_all)
        root.addLayout(versions_head)
        self.versions = QTableWidget(0, 3)
        self.versions.setObjectName("versionTable")
        self.versions.horizontalHeader().hide()
        self.versions.verticalHeader().hide()
        self.versions.setShowGrid(False)
        self.versions.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.versions.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.versions.setColumnWidth(0, 62)
        self.versions.setColumnWidth(1, 210)
        self.versions.horizontalHeader().setStretchLastSection(True)
        self.versions.itemSelectionChanged.connect(self._emit_version)
        root.addWidget(self.versions, 1)
        note_head = QHBoxLayout()
        note_title = QLabel("备注")
        note_title.setObjectName("sectionTitle")
        autosave = QLabel("自动保存")
        autosave.setObjectName("sectionMuted")
        note_head.addWidget(note_title)
        note_head.addStretch(1)
        note_head.addWidget(autosave)
        root.addLayout(note_head)
        self.note = QLineEdit()
        self.note.setPlaceholderText("添加备注…")
        self.note.setFixedHeight(58)
        self.note.editingFinished.connect(lambda: self.note_committed.emit(self.note.text()))
        root.addWidget(self.note)

    def _emit_version(self) -> None:
        rows = self.versions.selectionModel().selectedRows()
        self.version_changed.emit(rows[0].row() if rows else -1)

    def set_cover_path(self, cover_path: Optional[str]) -> None:
        pixmap = QPixmap(str(cover_path)) if cover_path else QPixmap()
        if pixmap.isNull():
            self.cover.setPixmap(_icon("fa6s.image", SWITCH["muted_strong"]).pixmap(52, 52))
            return
        self.cover.setPixmap(_scaled_pixmap_for_dpr(pixmap, self.cover.size(), self.devicePixelRatioF()))

    def set_entry(self, state: AppState, entry: SaveEntry) -> list[Snapshot]:
        result = state.resolve_save_identity(entry)
        identity = result.identity
        metadata = state.cached_save_metadata(identity)
        name = getattr(metadata, "canonical_title", "") or (identity.title if identity else "") or entry.display_name or Path(entry.path).name
        subtitle = ""
        if identity and identity.title and identity.title != name:
            subtitle = identity.title
        self.name.setText(name)
        self.subtitle.setText(subtitle)
        self.subtitle.setVisible(bool(subtitle))
        self.platform.setText(PLATFORM_LABELS.get(entry.platform, entry.platform))
        status = state.save_status(entry)
        versions = state.versions_for_entry(entry)
        self.fields["title_id"].setText((identity.title_id if identity else None) or entry.title_id or "—")
        self.fields["status"].setText({"new": "新", "changed": "有变化", "unchanged": "已备份"}.get(status.status, status.status))
        self.fields["versions"].setText(f"{len(versions)} 个版本")
        self.fields["size"].setText(self._entry_size(entry))
        self.fields["last_backup"].setText(_fmt_time(status.last_backup_at))
        self.fields["path"].setText(_short_path(entry.path))
        self.fields["path"].setToolTip(str(entry.path))
        resolution = state.resolve_save_cover(entry, result=result)
        self.set_cover_path(resolution.path)
        needs_binding = result.status == STATUS_AMBIGUOUS or result.status not in (STATUS_RESOLVED, STATUS_PARTIAL)
        self.warning.setVisible(needs_binding)
        self.warning_title.setText("多个 ROM 候选" if result.status == STATUS_AMBIGUOUS else "未绑定 ROM")
        self.versions.setRowCount(len(versions))
        for row, snapshot in enumerate(reversed(versions)):
            number = len(versions) - row
            first = QTableWidgetItem(f"●   v{number}")
            first.setForeground(QColor(SWITCH["accent"] if row == 0 else SWITCH["muted_strong"]))
            self.versions.setItem(row, 0, first)
            self.versions.setItem(row, 1, QTableWidgetItem(_fmt_time(snapshot.created_at)))
            self.versions.setItem(row, 2, QTableWidgetItem(self._snapshot_size(state, snapshot)))
        self.version_count.setText(f"{len(versions)} 个版本")
        if versions:
            self.versions.selectRow(0)
        self.note.blockSignals(True)
        self.note.setText(state.game_note(entry))
        self.note.blockSignals(False)
        self.primary.setText("删除备份" if state.library_mode else "备份存档")
        self.primary.setProperty("dangerPrimary", state.library_mode)
        self.primary.style().unpolish(self.primary)
        self.primary.style().polish(self.primary)
        return list(reversed(versions))

    @staticmethod
    def _entry_size(entry: SaveEntry) -> str:
        path = Path(entry.path)
        try:
            if path.is_file():
                size = path.stat().st_size
            else:
                size = sum(child.stat().st_size for child in path.rglob("*") if child.is_file() and not child.is_symlink())
            return DetailDrawer._format_bytes(size)
        except OSError:
            return "—"

    @staticmethod
    def _snapshot_size(state: AppState, snapshot: Snapshot) -> str:
        synthetic = SaveEntry("", "", "", str(snapshot.absolute_path(state.library_root)))
        return DetailDrawer._entry_size(synthetic)

    @staticmethod
    def _format_bytes(size: int) -> str:
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.0f} MB"
        if size >= 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size} B"


class LLMSettingsDialog(QDialog):
    """可选的 LLM 封面消歧设置，完整复用 AppState 的持久化能力。"""

    def __init__(self, state: AppState, loader: ArtworkLoader, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state
        self.loader = loader
        self.setWindowTitle("LLM 封面消歧")
        self.setMinimumWidth(520)
        form = QFormLayout(self)
        self.enabled = QCheckBox("启用（仅在封面候选存在歧义时使用）")
        self.enabled.setChecked(state.llm_cover_enabled)
        self.protocol = QComboBox()
        labels = {
            "openai-completions": "OpenAI 兼容 Chat Completions",
            "anthropic-messages": "Anthropic Messages",
        }
        for value in LLM_PROTOCOLS:
            self.protocol.addItem(labels.get(value, value), value)
        self.protocol.setCurrentIndex(max(0, self.protocol.findData(state.llm_protocol)))
        self.base_url = QLineEdit(state.llm_base_url)
        self.api_key = QLineEdit(state.llm_api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.model = QLineEdit(state.llm_model)
        form.addRow("", self.enabled)
        form.addRow("协议", self.protocol)
        form.addRow("Base URL", self.base_url)
        form.addRow("API 密钥", self.api_key)
        form.addRow("模型 ID", self.model)
        test_row = QHBoxLayout()
        self.test_button = _button("测试连接", "fa6s.plug-circle-check")
        self.test_result = QLabel("")
        self.test_result.setObjectName("sectionMuted")
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)
        form.addRow("", test_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._last_protocol = self.protocol.currentData()
        self.protocol.currentIndexChanged.connect(self._protocol_changed)
        self.test_button.clicked.connect(self._test_connection)

    def _protocol_changed(self) -> None:
        new_protocol = self.protocol.currentData()
        old_protocol = self._last_protocol
        if self.base_url.text().strip().rstrip("/") == default_base_url(old_protocol).rstrip("/"):
            self.base_url.setText(default_base_url(new_protocol))
        if self.model.text().strip() == default_model(old_protocol):
            self.model.setText(default_model(new_protocol))
        self._last_protocol = new_protocol

    def _values(self) -> dict[str, object]:
        return {
            "enabled": self.enabled.isChecked(),
            "protocol": self.protocol.currentData(),
            "base_url": self.base_url.text().strip(),
            "api_key": self.api_key.text().strip(),
            "model": self.model.text().strip(),
        }

    def _save(self) -> None:
        self.state.set_llm_cover(**self._values())
        self.accept()

    def _test_connection(self) -> None:
        values = self._values()
        self.test_button.setEnabled(False)
        self.test_result.setText("正在测试…")

        def task():
            return self.state.test_llm_cover(
                protocol=str(values["protocol"]),
                base_url=str(values["base_url"]),
                api_key=str(values["api_key"]),
                model=str(values["model"]),
            )

        def completed(result) -> None:
            self.test_button.setEnabled(True)
            ok, detail = result if result is not None else (False, "测试失败")
            self.test_result.setText(("✓ " if ok else "✗ ") + detail)

        self.loader.submit(("llm-probe", id(self)), task, completed)


class VajSaveWindow(QMainWindow):
    """参考图对应的雾银收藏架主窗口。"""

    def __init__(self, state: Optional[AppState] = None) -> None:
        super().__init__()
        self.state = state or AppState()
        self._selected: Optional[SaveEntry] = None
        self._versions: list[Snapshot] = []
        self._selected_snapshot: Optional[Snapshot] = None
        self._sort_mode = "recent"
        self._drawer_open = False
        self._drawer_animation: Optional[QPropertyAnimation] = None
        self._callback_bridge = _QtCallbackBridge(self)
        self._artwork_loader = ArtworkLoader(self._callback_bridge.dispatch.emit)
        self._enrichment_attempted: set[str] = set()
        self._list_generation = 0
        self.setWindowTitle("vaj-save")
        self.resize(1480, 900)
        self.setMinimumSize(1180, 700)
        self._build_ui()
        self._wire_events()
        self._apply_style()
        self.state.refresh_volumes()
        self.refresh_all()
        self.state.start_watch()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(250)
        self.poll_timer.timeout.connect(self._poll_state)
        self.poll_timer.start()
        QTimer.singleShot(80, self._select_initial_device)

    def _build_ui(self) -> None:
        shell = QWidget()
        shell.setObjectName("appShell")
        self.setCentralWidget(shell)
        outer = QVBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        topbar = QFrame()
        topbar.setObjectName("topbar")
        topbar.setFixedHeight(TOPBAR_HEIGHT)
        top = QHBoxLayout(topbar)
        top.setContentsMargins(22, 8, 20, 8)
        top.setSpacing(12)
        menu = QToolButton()
        menu.setObjectName("menuButton")
        menu.setIcon(_icon("fa6s.bars", SWITCH["ink"], 0.95))
        menu.setIconSize(QSize(25, 25))
        menu.setFixedSize(42, 42)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        brand_name = QLabel("vaj-save")
        brand_name.setObjectName("brandName")
        brand_tag = QLabel("守护每一段游戏时光")
        brand_tag.setObjectName("brandTag")
        brand.addWidget(brand_name)
        brand.addWidget(brand_tag)
        top.addWidget(menu)
        top.addLayout(brand)
        top.addStretch(1)
        self.search = QLineEdit()
        self.search.setObjectName("searchField")
        self.search.setPlaceholderText("搜索游戏名、Title ID 或其他信息…")
        self.search.addAction(_icon("fa6s.magnifying-glass", SWITCH["muted_strong"]), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setFixedWidth(360)
        self.search.setFixedHeight(38)
        top.addWidget(self.search)
        self.sort = _button("最近备份时间", "fa6s.arrow-down-wide-short")
        self.sort.setFixedHeight(38)
        self.only_updates = _button("仅显示有更新", "fa6s.square", checkable=True)
        self.only_updates.setFixedHeight(38)
        self.settings = _button("设置", "fa6s.gear")
        self.help = _button("帮助", "fa6s.circle-question")
        for button in (self.sort, self.only_updates, self.settings, self.help):
            top.addWidget(button)
        self.stats = QLabel("已备份 0 款游戏 · 0 个版本")
        self.stats.setObjectName("statsLabel")
        top.addWidget(self.stats)
        outer.addWidget(topbar)
        self.body = QWidget()
        self.body.setObjectName("body")
        base_row = QHBoxLayout(self.body)
        base_row.setContentsMargins(0, 0, 0, 0)
        base_row.setSpacing(0)
        self.dock = PlatformDock()
        self.gallery = GalleryView(self.state)
        base_row.addWidget(self.dock)
        base_row.addWidget(self.gallery, 1)
        self.drawer = DetailDrawer(self.body)
        self.drawer.hide()
        outer.addWidget(self.body, 1)
        status = QFrame()
        status.setObjectName("bottomBar")
        status.setFixedHeight(BOTTOMBAR_HEIGHT)
        status_row = QHBoxLayout(status)
        status_row.setContentsMargins(22, 0, 22, 0)
        status_row.setSpacing(12)
        self.status_device = QLabel("●  未连接设备")
        self.status_device.setObjectName("statusDevice")
        self.status_text = QLabel("准备好了，插上掌机或打开文件夹就可以开始")
        self.status_text.setObjectName("statusText")
        self.watch = _button("监听已开启", "fa6s.circle", checkable=True)
        self.watch.setChecked(True)
        self.watch.setObjectName("watchButton")
        status_row.addWidget(self.status_device)
        status_row.addWidget(self.status_text, 1)
        status_row.addWidget(self.watch)
        outer.addWidget(status)

    def _wire_events(self) -> None:
        self.search.textChanged.connect(self._search_changed)
        self.sort.clicked.connect(self._cycle_sort)
        self.only_updates.clicked.connect(self._toggle_updates)
        self.settings.clicked.connect(self._show_settings)
        self.help.clicked.connect(self._show_help)
        self.watch.clicked.connect(self._toggle_watch)
        self.dock.platform_selected.connect(self._select_platform)
        self.dock.refresh_requested.connect(self._refresh_devices)
        self.dock.add_requested.connect(self._add_device)
        self.dock.devices_requested.connect(self._choose_device)
        self.dock.ftp_requested.connect(self._show_ftp)
        self.dock.library_requested.connect(self._browse_library)
        self.gallery.selection_changed.connect(self._selection_changed)
        self.gallery.activated.connect(lambda _entry: self._primary_action())
        self.drawer.close_requested.connect(self.hide_drawer)
        self.drawer.backup_requested.connect(self._primary_action)
        self.drawer.restore_requested.connect(self._restore)
        self.drawer.export_requested.connect(self._export)
        self.drawer.location_requested.connect(self._open_location)
        self.drawer.bind_requested.connect(self._bind_rom)
        self.drawer.version_changed.connect(self._version_changed)
        self.drawer.note_committed.connect(self._save_note)

    def _apply_style(self) -> None:
        self.setStyleSheet(f"""
            * {{ font-family: '{FONT_FAMILY}'; font-size: 13px; color: {SWITCH['ink']}; }}
            #appShell, #body {{ background: {SWITCH['fog_canvas']}; }}
            #topbar {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {TOPBAR_TOP}, stop:1 {SWITCH['fog_top']}); border-bottom: 1px solid {SWITCH['border_soft']}; }}
            #menuButton {{ background: {SWITCH['dock_selected']}; border: 0; border-radius: 9px; }}
            #brandName {{ font-size: 17px; font-weight: 700; }}
            #brandTag, #statsLabel, #detailSubtitle, #fieldKey, #sectionMuted, #statusText {{ color: {SWITCH['muted_strong']}; }}
            #brandTag, #statsLabel {{ font-size: 11px; }}
            QLineEdit, QTableWidget, QListWidget, QComboBox {{ background: {CONTROL_WHITE}; border: 1px solid {SWITCH['border_soft']}; border-radius: 7px; padding: 7px 10px; selection-background-color: {SWITCH['selected']}; }}
            #searchField {{ background: {SWITCH['surface_alt']}; border-radius: 9px; }}
            QPushButton {{ background: {CONTROL_WHITE}; border: 1px solid {SWITCH['border_soft']}; border-radius: 7px; padding: 7px 12px; min-height: 20px; }}
            QPushButton:hover {{ background: {SWITCH['card']}; border-color: {mix(SWITCH['border_soft'], SWITCH['shadow_deep'], 0.34)}; }}
            QPushButton:pressed, QPushButton:checked {{ background: {SWITCH['selected_soft']}; border-color: {SWITCH['accent']}; }}
            QPushButton[primary='true'] {{ color: {SWITCH['on_accent']}; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {mix(SWITCH['accent'], SWITCH['card'], 0.08)}, stop:1 {darken(SWITCH['accent'], 0.06)}); border: 0; font-size: 14px; font-weight: 600; }}
            QPushButton[primary='true']:hover {{ background: {SWITCH['accent_hover']}; }}
            QPushButton[dangerPrimary='true'] {{ background: {SWITCH['danger']}; }}
            #platformDock {{ background: {SWITCH['fog_panel']}; border-right: 1px solid {SWITCH['border_soft']}; }}
            QToolButton[platform='true'] {{ background: transparent; border: 0; border-radius: 9px; padding: 4px 2px; font-size: 11px; }}
            QToolButton[platform='true']:checked {{ background: {SWITCH['dock_selected']}; color: {SWITCH['accent']}; }}
            QToolButton[dockAction='true'] {{ background: transparent; border: 0; text-align: left; padding: 6px 4px; font-size: 11px; }}
            QToolButton[dockAction='true']:hover {{ background: {SWITCH['surface_alt']}; border-radius: 6px; }}
            #dockDivider {{ color: {SWITCH['border_soft']}; }}
            #deviceDot {{ color: {SWITCH['muted_strong']}; font-size: 10px; }}
            #deviceDot[connected='true'], #statusDevice {{ color: {SWITCH['status_green']}; }}
            #deviceName {{ font-size: 10px; }}
            #galleryView {{ background: {SWITCH['fog_canvas']}; }}
            #detailDrawer {{ background: {SWITCH['fog_panel']}; border: 1px solid {mix(SWITCH['border_soft'], SWITCH['shadow_soft'], 0.35)}; border-radius: 10px 0 0 10px; }}
            #drawerClose {{ background: transparent; border: 0; padding: 4px; }}
            #drawerClose:hover {{ background: {SWITCH['surface_alt']}; border-radius: 6px; }}
            #drawerTitle, #sectionTitle {{ font-size: 15px; font-weight: 700; }}
            #detailName {{ font-size: 17px; font-weight: 700; }}
            #detailSubtitle {{ font-size: 11px; }}
            #detailCover {{ background: white; border: 1px solid {SWITCH['border_soft']}; border-radius: 6px; }}
            #platformChip {{ color: {SWITCH['accent']}; background: {SWITCH['selected_soft']}; border-radius: 6px; padding: 3px 12px; font-size: 11px; }}
            #fieldKey, #fieldValue {{ font-size: 11px; }}
            #romWarning {{ background: {WARNING_SURFACE}; border: 1px solid {WARNING_BORDER}; border-radius: 8px; }}
            #warningTitle {{ color: {WARNING_TEXT}; font-size: 13px; font-weight: 700; }}
            #warningHint {{ color: {SWITCH['muted_strong']}; font-size: 10px; }}
            #linkLabel {{ color: {SWITCH['accent']}; font-size: 11px; }}
            #versionTable {{ padding: 0; border-radius: 7px; gridline-color: transparent; }}
            #versionTable::item {{ padding: 5px 7px; border-bottom: 1px solid {mix(SWITCH['border_soft'], SWITCH['card'], 0.45)}; }}
            #versionTable::item:selected {{ background: {SWITCH['selected']}; color: {SWITCH['ink']}; }}
            #bottomBar {{ background: {CONTROL_WHITE}; border-top: 1px solid {SWITCH['border_soft']}; }}
            #statusDevice, #statusText, #watchButton {{ font-size: 11px; }}
        """)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_drawer(final=True)

    def _place_drawer(self, final: bool = False) -> None:
        if not hasattr(self, "drawer"):
            return
        height = self.body.height()
        x = self.body.width() - DRAWER_WIDTH if self._drawer_open else self.body.width()
        if final or self._drawer_animation is None:
            self.drawer.setGeometry(x, 0, DRAWER_WIDTH, height)
        else:
            self.drawer.resize(DRAWER_WIDTH, height)

    def show_drawer(self) -> None:
        was_open = self._drawer_open
        self._drawer_open = True
        self.drawer.resize(DRAWER_WIDTH, self.body.height())
        self.drawer.show()
        self.drawer.raise_()
        self.gallery.set_reserved_right(DRAWER_WIDTH)
        if was_open:
            self.drawer.move(self.body.width() - DRAWER_WIDTH, 0)
            return
        start = QPoint(self.body.width(), 0)
        end = QPoint(self.body.width() - DRAWER_WIDTH, 0)
        self._animate_drawer(start, end)

    def hide_drawer(self) -> None:
        if not self._drawer_open:
            return
        self._drawer_open = False
        start = self.drawer.pos()
        end = QPoint(self.body.width(), 0)
        animation = self._animate_drawer(start, end)
        animation.finished.connect(self.drawer.hide)
        animation.finished.connect(lambda: self.gallery.set_reserved_right(0))

    def _animate_drawer(self, start: QPoint, end: QPoint) -> QPropertyAnimation:
        animation = QPropertyAnimation(self.drawer, b"pos", self)
        animation.setDuration(190)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._drawer_animation = animation
        animation.start()
        return animation

    def _visible_saves(self) -> list[SaveEntry]:
        saves = list(self.state.visible_saves())
        if self._sort_mode == "name":
            saves.sort(key=lambda item: (item.display_name or "").casefold())
        elif self._sort_mode == "platform":
            saves.sort(key=lambda item: (item.platform, (item.display_name or "").casefold()))
        else:
            saves.sort(key=lambda item: self.state.save_status(item).last_backup_at or "", reverse=True)
        return saves

    def refresh_all(self) -> None:
        saves = self._visible_saves()
        self.gallery.set_entries(saves)
        self._list_generation += 1
        generation = self._list_generation
        QTimer.singleShot(
            0,
            lambda current=list(saves), current_generation=generation: self._schedule_cover_enrichment(
                current, current_generation
            ),
        )
        self.dock.set_current(self.state.selected_platform)
        stats = self.state.collection_stats()
        self.stats.setText(f"已备份 {stats.get('games', 0)} 款游戏 · {stats.get('versions', 0)} 个版本")
        current = next((v for v in self.state.volumes if Path(v.mount_point) == self.state.current_mount), None)
        connected = current is not None and not self.state.library_mode
        name = "本地存档" if self.state.library_mode else (current.name if current else "未连接")
        self.dock.set_device(name, connected or self.state.library_mode)
        self.status_device.setText(f"●  {name}")
        self.status_text.setText(self.state.status_text)

    def _schedule_cover_enrichment(
        self, saves: list[SaveEntry], generation: int
    ) -> None:
        """首帧之后为可见条目恢复元数据解析和高清封面下载。"""
        if generation != self._list_generation or self.state.library_mode:
            return
        pending = [save for save in saves if save.path not in self._enrichment_attempted]
        if not pending:
            return
        results = self.state.resolve_identities(pending)
        for save, result in zip(pending, results):
            if not result.is_resolved or result.identity is None:
                continue
            self._enrichment_attempted.add(save.path)
            identity = result.identity

            def task(entry=save, resolved=result, game_identity=identity):
                metadata = self.state.resolve_save_metadata(entry, game_identity)
                cover = self.state.ensure_save_cover(entry, resolved, metadata)
                return metadata, cover

            def completed(payload, entry=save) -> None:
                self._apply_cover_enrichment(entry, payload)

            self._artwork_loader.submit(("cover", save.path), task, completed)

    def _apply_cover_enrichment(self, entry: SaveEntry, payload) -> None:
        if not payload:
            return
        metadata, cover = payload
        cover_path = getattr(cover, "path", None)
        if cover_path:
            self.gallery.canvas.set_cover_path(entry.path, cover_path)
        if self._selected is None or self._selected.path != entry.path:
            return
        canonical_title = getattr(metadata, "canonical_title", "") if metadata else ""
        if canonical_title:
            self.drawer.name.setText(canonical_title)
        if cover_path:
            self.drawer.set_cover_path(cover_path)

    def _select_initial_device(self) -> None:
        if self.state.ensure_mount_selected() is not None:
            self.refresh_all()

    def _poll_state(self) -> None:
        if self.state.drain_events():
            self.refresh_all()

    def _search_changed(self, text: str) -> None:
        self.state.set_search_query(text)
        self.refresh_all()

    def _select_platform(self, platform: str) -> None:
        self.state.set_platform_filter(platform)
        self.refresh_all()

    def _cycle_sort(self) -> None:
        modes = ("recent", "name", "platform")
        labels = {"recent": "最近备份时间", "name": "名称", "platform": "平台"}
        self._sort_mode = modes[(modes.index(self._sort_mode) + 1) % len(modes)]
        self.sort.setText(labels[self._sort_mode])
        self.refresh_all()

    def _toggle_updates(self) -> None:
        self.state.toggle_hide_unchanged()
        self.only_updates.setChecked(self.state.hide_unchanged)
        self.refresh_all()

    def _selection_changed(self, entries: list[SaveEntry]) -> None:
        if not entries:
            self._selected = None
            self.hide_drawer()
            return
        self._selected = entries[-1]
        self._versions = self.drawer.set_entry(self.state, self._selected)
        self._selected_snapshot = self._versions[0] if self._versions else None
        self.show_drawer()

    def _version_changed(self, row: int) -> None:
        self._selected_snapshot = self._versions[row] if 0 <= row < len(self._versions) else None

    def _primary_action(self) -> None:
        selected = self.gallery.selected_entries()
        if not selected:
            return
        if self.state.library_mode:
            names = "、".join(item.display_name for item in selected[:3])
            if len(selected) > 3:
                names += f" 等 {len(selected)} 款"
            if QMessageBox.question(self, "删除本地备份", f"确定删除 {names} 的全部本地版本吗？\n此操作不可撤销。") != QMessageBox.StandardButton.Yes:
                return
            for entry in selected:
                self.state.delete_library_game(entry)
        else:
            self.state.import_selected_saves(selected)
        self.refresh_all()
        if self._selected:
            self._versions = self.drawer.set_entry(self.state, self._selected)

    def _restore(self) -> None:
        if self._selected_snapshot is None:
            QMessageBox.information(self, "恢复", "请先选择一个备份版本。")
            return
        destination = QFileDialog.getExistingDirectory(self, "选择恢复位置")
        if not destination:
            return
        if QMessageBox.question(self, "确认恢复", "将所选版本复制到指定文件夹？不会写入掌机。") != QMessageBox.StandardButton.Yes:
            return
        self.state.restore_version(self._selected_snapshot, destination)
        self.refresh_all()

    def _export(self) -> None:
        if self._selected_snapshot is None:
            QMessageBox.information(self, "导出 ZIP", "请先选择一个备份版本。")
            return
        default = f"{self._selected.display_name if self._selected else 'backup'}-{self._selected_snapshot.id}.zip"
        path, _ = QFileDialog.getSaveFileName(self, "导出 ZIP", default, "ZIP 文件 (*.zip)")
        if path:
            self.state.export_version_zip(self._selected_snapshot, path)
            self.refresh_all()

    def _open_location(self) -> None:
        if not self._selected:
            return
        path = self._selected_snapshot.absolute_path(self.state.library_root) if self._selected_snapshot else Path(self._selected.path)
        ok, message = _open_path(path)
        self.status_text.setText(message)
        if not ok:
            QMessageBox.warning(self, "打开位置", message)

    def _bind_rom(self) -> None:
        if not self._selected:
            return
        exts = supported_extensions(self._selected.platform)
        filter_text = "ROM 文件 (" + " ".join(f"*{ext}" for ext in exts) + ")" if exts else "所有文件 (*)"
        path, _ = QFileDialog.getOpenFileName(self, "选择对应 ROM", "", filter_text)
        if not path:
            return
        try:
            self.state.bind_save_identity(self._selected, rom_path=path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "ROM 绑定失败", str(exc))
            return
        self._versions = self.drawer.set_entry(self.state, self._selected)
        self._enrichment_attempted.discard(self._selected.path)
        self.refresh_all()

    def _save_note(self, note: str) -> None:
        if self._selected:
            self.state.set_note(self._selected, note)
            self.status_text.setText(self.state.status_text)

    def _refresh_devices(self) -> None:
        self._enrichment_attempted.clear()
        self.state.refresh_volumes()
        self.state.ensure_mount_selected()
        self.refresh_all()

    def _add_device(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "添加设备或存档目录")
        if path:
            self.state.select_custom_path(path)
            self.refresh_all()

    def _choose_device(self) -> None:
        self.state.refresh_volumes()
        dialog = QDialog(self)
        dialog.setWindowTitle("选择设备")
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        for volume in self.state.volumes:
            item = QListWidgetItem(f"{volume.name}\n{volume.mount_point}")
            item.setData(Qt.ItemDataRole.UserRole, str(volume.mount_point))
            listing.addItem(item)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() and listing.currentItem():
            self.state.select_mount(listing.currentItem().data(Qt.ItemDataRole.UserRole))
            self.refresh_all()

    def _browse_library(self) -> None:
        self.state.set_library_mode(not self.state.library_mode)
        self.refresh_all()

    def _toggle_watch(self) -> None:
        if self.watch.isChecked():
            self.state.start_watch()
            self.watch.setText("监听已开启")
        else:
            self.state.stop_watch(timeout=0.5)
            self.watch.setText("监听已关闭")

    def _show_ftp(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("FTP 只读拉取")
        form = QFormLayout(dialog)
        preset = QComboBox()
        profiles = self.state.ftp_presets()
        for profile in profiles:
            preset.addItem(profile.label, profile.key)
        preset.setCurrentIndex(max(0, preset.findData(self.state.ftp_preset_key)))
        host = QLineEdit(self.state.ftp_host)
        port = QLineEdit(str(self.state.ftp_port or ""))
        user = QLineEdit(self.state.ftp_user)
        password = QLineEdit()
        password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("预设", preset)
        form.addRow("主机", host)
        form.addRow("端口", port)
        form.addRow("用户", user)
        form.addRow("密码", password)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            self.state.configure_ftp(host.text(), port.text(), user.text(), password.text(), preset.currentData())
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                result = self.state.pull_ftp_saves()
            finally:
                QApplication.restoreOverrideCursor()
            self.refresh_all()
            if not result.ok:
                QMessageBox.warning(self, "FTP 拉取失败", result.error or "未知错误")

    def _show_settings(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("设置")
        dialog.setMinimumWidth(560)
        form = QFormLayout(dialog)
        library = QLineEdit(str(self.state.library_root))
        gba = QLineEdit(str(self.state.gba_rom_dir or ""))
        nds = QLineEdit(str(self.state.nds_rom_dir or ""))
        libretro = QLineEdit(str(self.state.libretro_dir or ""))
        libretro.setObjectName("libretroDirectory")
        keep = QLineEdit(str(load_keep_last(self.state.library_root)))
        form.addRow("本地备份库", library)
        form.addRow("GBA ROM 目录", gba)
        form.addRow("NDS ROM 目录", nds)
        form.addRow("Libretro 元数据目录", libretro)
        form.addRow("保留版本数（0 为不限）", keep)
        llm_entry = _button(
            "配置…（已启用）" if self.state.llm_cover_enabled else "配置…（未启用）",
            "fa6s.wand-magic-sparkles",
        )
        llm_entry.setObjectName("llmSettingsEntry")

        def open_llm_settings() -> None:
            if self._show_llm_settings(dialog):
                llm_entry.setText(
                    "配置…（已启用）" if self.state.llm_cover_enabled else "配置…（未启用）"
                )

        llm_entry.clicked.connect(open_llm_settings)
        form.addRow("LLM 封面消歧", llm_entry)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            self.state.set_library_root(library.text())
            self.state.set_rom_dirs(gba.text(), nds.text())
            self.state.set_libretro_dir(libretro.text())
            if self.state.set_keep_last(keep.text()) is None:
                QMessageBox.warning(self, "设置", "保留版本数必须是非负整数。")
            self._enrichment_attempted.clear()
            self.refresh_all()

    def _show_llm_settings(self, parent: Optional[QWidget] = None) -> bool:
        dialog = LLMSettingsDialog(self.state, self._artwork_loader, parent or self)
        saved = dialog.exec() == QDialog.DialogCode.Accepted
        if saved:
            self._enrichment_attempted.clear()
            self.refresh_all()
            if self.state.llm_cover_enabled and not self.state.llm_api_key:
                self.status_text.setText("已开启 LLM 封面消歧（未填 API 密钥，暂不生效）")
            else:
                self.status_text.setText("LLM 封面消歧设置已更新")
        return saved

    def _show_help(self) -> None:
        QMessageBox.information(
            self,
            "帮助",
            "vaj-save 将掌机存档只读备份到电脑。\n\n"
            "选择游戏后可备份、恢复到指定文件夹、导出 ZIP、绑定 ROM 或记录备注。\n"
            "恢复不会直接写回掌机。",
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        self.poll_timer.stop()
        self._artwork_loader.shutdown(wait=False)
        self.state.stop_watch(timeout=0.5)
        super().closeEvent(event)


def build_app(state: Optional[AppState] = None) -> VajSaveWindow:
    """构建 Qt 主窗口；调用方负责持有 QApplication。"""
    return VajSaveWindow(state=state)


def run_app(state: Optional[AppState] = None) -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("vaj-save")
    app.setOrganizationName("vaj-save")
    app.setFont(QFont(FONT_FAMILY, 10))
    window = build_app(state)
    window.show()
    return app.exec()


__all__ = ["DetailDrawer", "GalleryCanvas", "GalleryView", "LLMSettingsDialog", "PlatformDock", "VajSaveWindow", "build_app", "run_app"]

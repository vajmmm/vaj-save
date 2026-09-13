"""PySide6 运行时的关键布局与交互回归测试。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from vajsave.app_state import AppState
from vajsave.models import SaveEntry, ScanResult, VolumeInfo
from vajsave.qt_ui import DRAWER_WIDTH, GalleryCanvas, VajSaveWindow, _platform_icon
from vajsave.volume import FakeVolumeProvider


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def qt_state(tmp_path: Path, monkeypatch):
    mount = tmp_path / "device"
    mount.mkdir()
    saves = []
    for index in range(8):
        path = mount / f"save-{index}"
        path.mkdir()
        (path / "data.bin").write_bytes(bytes([index]) * 16)
        saves.append(
            SaveEntry(
                platform="switch" if index < 4 else "3ds",
                source_id="demo",
                display_name=f"游戏 {index}",
                path=str(path),
                title_id=f"0100{index:012d}",
            )
        )
    result = ScanResult(root_path=str(mount), platform="switch", saves=saves)
    volume = VolumeInfo("测试掌机", mount, True)
    state = AppState(
        provider=FakeVolumeProvider([volume]),
        scan_fn=lambda _path: result,
        library_root=tmp_path / "library",
    )
    state.refresh_volumes()
    state.select_mount(mount)
    monkeypatch.setattr(state, "start_watch", lambda *args, **kwargs: None)
    monkeypatch.setattr(state, "stop_watch", lambda *args, **kwargs: None)
    return state


def test_qt_window_uses_reference_dimensions_and_overlay(qt_app, qt_state):
    window = VajSaveWindow(qt_state)
    try:
        window.show()
        QTest.qWait(60)
        window.gallery.canvas.selected = {0}
        window.gallery.canvas.active = 0
        window.gallery.canvas.selection_changed.emit(window.gallery.canvas.selected_entries())
        QTest.qWait(240)
        assert window.size().width() == 1480
        assert window.size().height() == 900
        assert window.drawer.width() == DRAWER_WIDTH
        assert window.drawer.x() == window.body.width() - DRAWER_WIDTH
        assert window.gallery.canvas.columns == 4
        assert window.drawer.isVisible()
    finally:
        window.close()


def test_gallery_supports_single_ctrl_range_and_keyboard_selection(qt_app, qt_state):
    canvas = GalleryCanvas(qt_state)
    canvas.resize(960, 700)
    canvas.set_entries(qt_state.visible_saves())
    canvas.show()
    QTest.qWait(30)
    first = canvas._case_rect(0).center().toPoint()
    third = canvas._case_rect(2).center().toPoint()
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=first)
    assert canvas.selected == {0}
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=third)
    assert canvas.selected == {0, 2}
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, pos=canvas._case_rect(1).center().toPoint())
    assert canvas.selected == {1, 2}
    QTest.keyClick(canvas, Qt.Key.Key_Right)
    assert canvas.active == 2
    canvas.close()


def test_formal_entry_uses_qt_runtime():
    source = Path(__file__).resolve().parents[1].joinpath("src/vajsave/app.py").read_text(encoding="utf-8")
    assert "from .qt_ui import run_app" in source
    assert "mainloop" not in source


def test_platform_icons_use_distinct_official_marks(qt_app):
    cache_keys = []
    for platform in ("all", "psp", "vita", "switch", "3ds", "nds", "gba"):
        pixmap = _platform_icon(platform).pixmap(68, 28)
        assert not pixmap.isNull()
        cache_keys.append(pixmap.cacheKey())
    assert len(set(cache_keys)) == 7


def test_platform_logo_assets_are_packaged():
    from importlib import resources

    data = resources.files("vajsave.data")
    for platform in ("psp", "vita", "switch", "3ds", "nds", "gba"):
        asset = data.joinpath(f"platform-{platform}.svg")
        assert asset.is_file()
        assert b"<svg" in asset.read_bytes()

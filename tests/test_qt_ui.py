"""PySide6 运行时的关键布局与交互回归测试。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

from vajsave.app_state import AppState
from vajsave.artwork import ArtworkResolution, SOURCE_DOWNLOADED
from vajsave.metadata import GameMetadata
from vajsave.models import SaveEntry, ScanResult, VolumeInfo
from vajsave.qt_ui import (
    DRAWER_WIDTH,
    GalleryCanvas,
    LLMSettingsDialog,
    VajSaveWindow,
    _cover_source_rect,
    _platform_icon,
    _render_platform_logo,
    _scaled_pixmap_for_dpr,
)
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
    monkeypatch.setattr(state, "resolve_save_metadata", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        state,
        "ensure_save_cover",
        lambda entry, result=None, metadata=None: state.resolve_save_cover(
            entry, result=result
        ),
    )
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

    for dpr in (1.0, 1.25, 1.5, 1.75, 2.0, 3.0, 4.0):
        hidpi = _platform_icon("switch").pixmap(QSize(68, 28), dpr)
        assert hidpi.size() == QSize(round(68 * dpr), round(28 * dpr))
        assert hidpi.devicePixelRatio() == dpr
        assert hidpi.deviceIndependentSize().toSize() == QSize(68, 28)


def test_platform_icons_remain_legible_at_100_percent(qt_app):
    for platform in ("psp", "vita", "switch", "3ds", "nds", "gba"):
        image = _platform_icon(platform).pixmap(QSize(68, 28), 1.0).toImage()
        strong_pixels = sum(
            image.pixelColor(x, y).alpha() >= 160
            for y in range(image.height())
            for x in range(image.width())
        )
        assert strong_pixels >= 90, platform


def test_platform_logo_assets_are_packaged():
    from importlib import resources

    data = resources.files("vajsave.data")
    for platform in ("psp", "vita", "switch", "3ds", "nds", "gba"):
        asset = data.joinpath(f"platform-{platform}.svg")
        assert asset.is_file()
        assert b"<svg" in asset.read_bytes()


@pytest.mark.parametrize("dpr", (1.0, 1.25, 1.5, 2.0, 3.0))
def test_platform_logo_renders_at_physical_dpi(qt_app, dpr):
    from importlib import resources

    svg = resources.files("vajsave.data").joinpath("platform-switch.svg").read_bytes()
    pixmap = _render_platform_logo("switch", svg, dpr)
    assert pixmap.width() == round(68 * dpr)
    assert pixmap.height() == round(28 * dpr)
    assert pixmap.devicePixelRatio() == dpr
    assert pixmap.deviceIndependentSize().toSize() == QSize(68, 28)


def test_cover_crop_uses_original_pixels(qt_app):
    portrait = QPixmap(300, 500)
    source = _cover_source_rect(portrait, QRectF(0, 0, 180, 280))
    assert source.width() == 300
    assert source.height() < 500
    assert source.center() == QRectF(0, 0, 300, 500).center()


@pytest.mark.parametrize("dpr", (1.25, 1.5, 2.0))
def test_detail_cover_scaling_preserves_logical_size(qt_app, dpr):
    source = QPixmap(600, 900)
    scaled = _scaled_pixmap_for_dpr(source, QSize(116, 196), dpr)
    assert scaled.devicePixelRatio() == dpr
    assert scaled.width() <= round(116 * dpr)
    assert scaled.height() <= round(196 * dpr)
    logical = scaled.deviceIndependentSize()
    assert logical.width() <= 116
    assert logical.height() <= 196


def test_qt_gallery_enriches_visible_covers_in_background(
    qt_app, qt_state, tmp_path: Path, monkeypatch
):
    cover = tmp_path / "high-resolution.png"
    image = QPixmap(900, 1200)
    image.fill(Qt.GlobalColor.blue)
    assert image.save(str(cover), "PNG")
    metadata_calls = []
    cover_calls = []

    def resolve_metadata(entry, identity=None):
        metadata_calls.append(entry.path)
        return GameMetadata(
            identity_key=identity.identity_key,
            platform=entry.platform,
            canonical_title=f"高清 {entry.display_name}",
        )

    def ensure_cover(entry, result=None, metadata=None):
        cover_calls.append(entry.path)
        return ArtworkResolution(str(cover), SOURCE_DOWNLOADED)

    monkeypatch.setattr(qt_state, "resolve_save_metadata", resolve_metadata)
    monkeypatch.setattr(qt_state, "ensure_save_cover", ensure_cover)
    window = VajSaveWindow(qt_state)
    try:
        window.show()
        QTest.qWait(160)
        first = qt_state.visible_saves()[0]
        assert first.path in metadata_calls
        assert first.path in cover_calls
        assert window.gallery.canvas._cover_paths[first.path] == str(cover)
    finally:
        window.close()


def test_llm_settings_dialog_preserves_all_configuration_fields(
    qt_app, qt_state, monkeypatch
):
    saved = {}

    class ImmediateLoader:
        def submit(self, key, task, callback):
            callback(task())
            return True

    monkeypatch.setattr(qt_state, "set_llm_cover", lambda **values: saved.update(values))
    dialog = LLMSettingsDialog(qt_state, ImmediateLoader())
    dialog.enabled.setChecked(True)
    dialog.api_key.setText("sk-test")
    dialog.base_url.setText("https://example.test/v1")
    dialog.model.setText("test-model")
    dialog._save()
    assert saved == {
        "enabled": True,
        "protocol": qt_state.llm_protocol,
        "base_url": "https://example.test/v1",
        "api_key": "sk-test",
        "model": "test-model",
    }
    assert dialog.result() == dialog.DialogCode.Accepted


def test_main_settings_exposes_libretro_and_llm_entries(qt_app, qt_state):
    window = VajSaveWindow(qt_state)
    found = {}

    def inspect_dialog():
        dialog = QApplication.activeModalWidget()
        found["libretro"] = dialog.findChild(QLineEdit, "libretroDirectory")
        found["llm"] = dialog.findChild(QPushButton, "llmSettingsEntry")
        dialog.reject()

    try:
        QTimer.singleShot(0, inspect_dialog)
        window._show_settings()
        assert found["libretro"] is not None
        assert found["llm"] is not None
    finally:
        window.close()

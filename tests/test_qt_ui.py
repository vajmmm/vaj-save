"""PySide6 运行时的关键布局与交互回归测试。"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QImage, QPainter, QPaintEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
)

from vajsave.app_state import PLATFORM_ORDER, AppState
from vajsave.baidu_api import BaiduCredentials
from vajsave.baidu_sync_dialog import BaiduSyncDialog
from vajsave import ui_theme
from vajsave.artwork import ArtworkResolution, PLACEHOLDER, SOURCE_DOWNLOADED
from vajsave.library import backup_save
from vajsave.metadata import GameMetadata
from vajsave.models import SaveEntry, ScanResult, VolumeInfo
from vajsave.qt_ui import (
    DRAWER_WIDTH,
    DetailDrawer,
    GalleryCanvas,
    LLMSettingsDialog,
    PlatformButton,
    VajSaveWindow,
    _cover_fit_rect,
    _cover_source_rect,
    _platform_icon,
    _render_platform_logo,
    _scaled_pixmap_for_dpr,
)
import vajsave.qt_ui as qt_ui
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
        columns_before = window.gallery.canvas.columns
        window.gallery.canvas.selected = {0}
        window.gallery.canvas.active = 0
        window.gallery.canvas.selection_changed.emit(window.gallery.canvas.selected_entries())
        QTest.qWait(240)
        assert window.size().width() == 1480
        assert window.size().height() == 900
        assert window.drawer.width() == DRAWER_WIDTH
        assert window.drawer.x() == window.body.width() - DRAWER_WIDTH
        # The drawer overlays the gallery: opening it must not re-flow columns.
        assert window.gallery.canvas.columns == columns_before
        assert window.gallery.canvas.reserved_right == 0
        assert window.drawer.isVisible()
    finally:
        window.close()


def test_baidu_sync_menu_and_credentials_dialog_are_isolated(qt_app, qt_state):
    window = VajSaveWindow(qt_state)
    try:
        menu_button = window.findChild(QToolButton, "menuButton")
        action_labels = [
            action.text()
            for action in menu_button.menu().actions()
            if not action.isSeparator()
        ]
        assert "配置百度网盘…" in action_labels
        assert "同步到百度网盘" in action_labels

        class CredentialStore:
            def load(self):
                return BaiduCredentials(
                    "app-key", "secret-key", "vaj-save", "access", "refresh", 0
                )

        dialog = BaiduSyncDialog(object(), CredentialStore(), window)
        try:
            assert dialog.app_secret.echoMode() == QLineEdit.EchoMode.Password
            assert dialog.auth_status.text() == "已连接百度网盘。"
            assert not dialog.open_browser_button.isEnabled()
        finally:
            dialog.reject()
    finally:
        window.close()


def test_initial_device_scan_keeps_qt_event_loop_responsive(
    qt_app, tmp_path: Path, monkeypatch
):
    mount = tmp_path / "slow-device"
    mount.mkdir()
    volume = VolumeInfo("慢速存储卡", mount, True)
    started = threading.Event()
    release = threading.Event()
    worker_threads = []
    result = ScanResult(root_path=str(mount), platform="vita", saves=[])

    def slow_scan(_path):
        worker_threads.append(threading.get_ident())
        started.set()
        release.wait(timeout=3)
        return result

    state = AppState(
        provider=FakeVolumeProvider([volume]),
        scan_fn=slow_scan,
        library_root=tmp_path / "library",
    )
    state.refresh_volumes()
    monkeypatch.setattr(state, "start_watch", lambda *args, **kwargs: None)
    monkeypatch.setattr(state, "stop_watch", lambda *args, **kwargs: None)
    window = VajSaveWindow(state)
    try:
        window.show()
        QTest.qWait(180)
        assert started.is_set()
        assert worker_threads != [threading.get_ident()]
        assert "正在扫描" in window.status_text.text()

        delivered = []
        QTimer.singleShot(0, lambda: delivered.append(True))
        QTest.qWait(50)
        assert delivered == [True]
        assert state.current_result is None

        release.set()
        for _ in range(30):
            QTest.qWait(20)
            if state.current_result is not None:
                break
        assert state.current_result is result
    finally:
        release.set()
        window.close()


def test_stale_background_scan_cannot_replace_new_device(
    qt_app, tmp_path: Path, monkeypatch
):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_started = threading.Event()
    release_first = threading.Event()

    def scan_fn(path):
        path = Path(path)
        if path == first:
            first_started.set()
            release_first.wait(timeout=3)
        return ScanResult(root_path=str(path), platform=path.name, saves=[])

    state = AppState(
        provider=FakeVolumeProvider([]),
        scan_fn=scan_fn,
        library_root=tmp_path / "library",
    )
    monkeypatch.setattr(state, "start_watch", lambda *args, **kwargs: None)
    monkeypatch.setattr(state, "stop_watch", lambda *args, **kwargs: None)
    window = VajSaveWindow(state)
    try:
        window._request_mount_scan(first)
        for _ in range(20):
            QTest.qWait(10)
            if first_started.is_set():
                break
        assert first_started.is_set()

        window._request_mount_scan(second)
        release_first.set()
        for _ in range(60):
            QTest.qWait(20)
            if state.current_result is not None:
                break

        assert state.current_mount == second
        assert state.current_result is not None
        assert state.current_result.root_path == str(second)
    finally:
        release_first.set()
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
    for platform in ("all", "psp", "vita", "switch", "3ds", "nds", "gb", "gbc", "gba"):
        pixmap = _platform_icon(platform).pixmap(68, 28)
        assert not pixmap.isNull()
        cache_keys.append(pixmap.cacheKey())
    assert len(set(cache_keys)) == 9

    for dpr in (1.0, 1.25, 1.5, 1.75, 2.0, 3.0, 4.0):
        hidpi = _platform_icon("switch").pixmap(QSize(68, 28), dpr)
        assert hidpi.size() == QSize(round(68 * dpr), round(28 * dpr))
        assert hidpi.devicePixelRatio() == dpr
        assert hidpi.deviceIndependentSize().toSize() == QSize(68, 28)


def test_platform_buttons_only_show_the_all_label(qt_app):
    all_button = PlatformButton("all")
    psp_button = PlatformButton("psp")

    assert all_button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextUnderIcon
    assert psp_button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert psp_button.toolTip() == "PSP"
    assert psp_button.accessibleName() == "PSP"


def test_platform_icons_remain_legible_at_100_percent(qt_app):
    for platform in ("psp", "vita", "switch", "3ds", "nds", "gb", "gbc", "gba"):
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
    for platform in ("psp", "vita", "switch", "3ds", "nds", "gb", "gbc", "gba"):
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


def test_psp_cover_fit_preserves_full_artwork(qt_app):
    portrait = QPixmap(600, 1000)
    target = QRectF(0, 0, 180, 280)
    fitted = _cover_fit_rect(portrait, target)
    assert fitted.height() == target.height()
    assert fitted.width() < target.width()
    assert fitted.center() == target.center()


def test_psp_gallery_uses_full_cover_fit(qt_app, qt_state, tmp_path: Path, monkeypatch):
    path = tmp_path / "psp-boxart.png"
    cover = QPixmap(600, 1000)
    cover.fill(Qt.GlobalColor.blue)
    assert cover.save(str(path), "PNG")
    entry = SaveEntry(
        platform="psp",
        source_id="psp-demo",
        display_name="PSP 游戏",
        path=str(tmp_path / "save"),
    )
    canvas = GalleryCanvas(qt_state)
    canvas.resize(320, 440)
    canvas.set_entries([entry])
    canvas.set_cover_path(entry.path, str(path))
    calls = []
    original = qt_ui._cover_fit_rect

    def record_fit(pixmap, target):
        calls.append(target)
        return original(pixmap, target)

    monkeypatch.setattr(qt_ui, "_cover_fit_rect", record_fit)
    image = QImage(320, 440, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    canvas._paint_case(painter, 0, entry)
    painter.end()
    assert calls


def test_switch_gallery_uses_full_cover_fit(qt_app, qt_state, tmp_path: Path, monkeypatch):
    path = tmp_path / "switch-boxart.png"
    cover = QPixmap(352, 570)
    cover.fill(Qt.GlobalColor.red)
    assert cover.save(str(path), "PNG")
    entry = SaveEntry(
        platform="switch",
        source_id="switch-demo",
        display_name="Switch 游戏",
        path=str(tmp_path / "save"),
    )
    canvas = GalleryCanvas(qt_state)
    canvas.resize(320, 440)
    canvas.set_entries([entry])
    canvas.set_cover_path(entry.path, str(path))
    calls = []
    original = qt_ui._cover_fit_rect

    def record_fit(pixmap, target):
        calls.append(target)
        return original(pixmap, target)

    monkeypatch.setattr(qt_ui, "_cover_fit_rect", record_fit)
    image = QImage(320, 440, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    canvas._paint_case(painter, 0, entry)
    painter.end()
    assert calls


def test_gallery_case_geometry_stays_portrait_for_supported_platforms(qt_app, qt_state):
    canvas = GalleryCanvas(qt_state)
    for platform in ("switch", "psp", "vita", "3ds", "nds", "gba", "gb", "gbc"):
        entry = SaveEntry(platform=platform, source_id="test", display_name=platform, path=platform)
        width, height = canvas._case_size(entry)
        assert width <= height, platform


def test_gallery_case_size_differs_between_switch_and_handhelds(qt_app, qt_state):
    canvas = GalleryCanvas(qt_state)

    def size(platform):
        entry = SaveEntry(platform=platform, source_id="test", display_name=platform, path=platform)
        return canvas._case_size(entry)

    switch = size("switch")
    nds = size("nds")
    gba = size("gba")
    gb = size("gb")
    # Switch is the tall reference case.
    assert switch[1] > nds[1]
    assert switch[1] > gba[1]
    assert switch[1] > gb[1]
    # Handheld cases are near-square portrait, not chopped Switch boxes.
    assert nds[0] / nds[1] >= 0.85
    assert gba[0] / gba[1] >= 0.85
    assert gb[0] < switch[0]


def test_gallery_case_size_is_platform_token_driven(qt_app, qt_state):
    canvas = GalleryCanvas(qt_state)
    entry = SaveEntry(platform="switch", source_id="test", display_name="switch", path="switch")
    baseline = canvas._case_size(entry)
    # Selection / hover must never inject a different case geometry.
    canvas.hovered = 0
    canvas.selected = {0}
    assert canvas._case_size(entry) == baseline


def test_detail_cover_adapts_box_shape_to_platform(qt_app, qt_state):
    drawer = DetailDrawer()
    try:
        drawer.set_cover_path(None, "switch")
        switch = drawer.cover.size()
        drawer.set_cover_path(None, "nds")
        nds = drawer.cover.size()
        drawer.set_cover_path(None, "gba")
        gba = drawer.cover.size()
        assert (switch.width(), switch.height()) != (116, 196)
        assert switch.height() > nds.height()
        assert switch.width() / switch.height() <= 0.80
        assert nds.width() <= nds.height()
        assert gba.width() <= gba.height()
        assert nds.width() / nds.height() >= 0.85
        assert gba.width() / gba.height() >= 0.85
    finally:
        drawer.deleteLater()


def test_detail_cover_uses_actual_near_square_aspect(qt_app, qt_state, tmp_path: Path):
    cover = tmp_path / "nds-cover.png"
    image = QPixmap(400, 420)
    image.fill(Qt.GlobalColor.blue)
    assert image.save(str(cover), "PNG")
    drawer = DetailDrawer()
    try:
        drawer.set_cover_path(str(cover), "nds")
        size = drawer.cover.size()
        # The control must roughly follow the 400x420 artwork instead of being
        # letterboxed inside a fixed 116x196 Switch frame.
        assert abs(size.width() / size.height() - 400 / 420) < 0.05
        assert (size.width(), size.height()) != (116, 196)
    finally:
        drawer.deleteLater()


def test_detail_cover_background_uses_theme_token(qt_app, qt_state):
    window = VajSaveWindow(qt_state)
    try:
        style = window.styleSheet()
        segment = style.split("#detailCover", 1)[1].split("}", 1)[0]
        assert "white" not in segment.lower()
        assert ui_theme.SWITCH["panel_alt"].lower() in segment.lower()
    finally:
        window.close()


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


def test_gallery_clears_landscape_cover_when_enrichment_falls_back(
    qt_app, qt_state
):
    window = VajSaveWindow(qt_state)
    try:
        entry = qt_state.visible_saves()[0]
        window.gallery.canvas.set_cover_path(entry.path, "/tmp/landscape-icon.png")
        assert window.gallery.canvas._cover_paths[entry.path] == "/tmp/landscape-icon.png"
        window._apply_cover_enrichment(entry, (None, PLACEHOLDER))
        assert window.gallery.canvas._cover_paths[entry.path] == ""
    finally:
        window.close()


def test_gallery_rejects_landscape_cover_pixmap(qt_app, qt_state, tmp_path: Path):
    path = tmp_path / "landscape-icon.png"
    landscape = QPixmap(400, 200)
    landscape.fill(Qt.GlobalColor.blue)
    assert landscape.save(str(path), "PNG")

    canvas = GalleryCanvas(qt_state)
    entry = qt_state.visible_saves()[0]
    canvas.set_cover_path(entry.path, str(path))
    assert canvas._load_pixmap(entry) is None
    assert canvas._cover_paths[entry.path] == ""


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


def test_identity_resolution_runs_off_qt_main_thread(qt_app, qt_state, monkeypatch):
    resolved_threads = []
    original_resolve = qt_state.resolve_identities

    def resolve_wrapper(entries=None):
        resolved_threads.append(threading.current_thread())
        return original_resolve(entries)

    monkeypatch.setattr(qt_state, "resolve_identities", resolve_wrapper)

    window = VajSaveWindow(qt_state)
    try:
        window.show()
        for _ in range(50):
            QTest.qWait(20)
            if resolved_threads:
                break
        assert resolved_threads, "resolve_identities was never called"
        assert resolved_threads[0] != threading.current_thread()
        assert resolved_threads[0] != threading.main_thread()
    finally:
        window.close()


def test_scan_completed_shows_saves_before_hash_finishes(
    qt_app, tmp_path: Path, monkeypatch
):
    mount = tmp_path / "dev"
    save_path = mount / "save1"
    save_path.mkdir(parents=True)
    (save_path / "data.bin").write_bytes(b"data1")
    lib = tmp_path / "lib"
    entry = SaveEntry(
        platform="switch",
        source_id="demo",
        display_name="Game 1",
        path=str(save_path),
        title_id="0100000000000001",
    )
    backup_save(entry, lib, datetime(2026, 1, 1, 10, 0, 0))

    hash_started = threading.Event()
    hash_release = threading.Event()
    from vajsave.library import hash_tree as real_hash_tree

    def slow_hash(path):
        hash_started.set()
        hash_release.wait(timeout=3)
        return real_hash_tree(path)

    monkeypatch.setattr("vajsave.app_state.hash_tree", slow_hash)

    scan_res = ScanResult(root_path=str(mount), platform="switch", saves=[entry])
    volume = VolumeInfo("Switch", mount, True)
    state = AppState(
        provider=FakeVolumeProvider([volume]),
        scan_fn=lambda _p: scan_res,
        library_root=lib,
    )
    monkeypatch.setattr(state, "start_watch", lambda *args, **kwargs: None)
    monkeypatch.setattr(state, "stop_watch", lambda *args, **kwargs: None)

    window = VajSaveWindow(state)
    try:
        window._request_mount_scan(mount)
        for _ in range(50):
            QTest.qWait(20)
            if hash_started.is_set():
                break

        assert hash_started.is_set()
        # Scan applied: saves visible in state and in gallery canvas
        assert state.current_result is not None
        assert len(state.current_result.saves) == 1
        assert len(window.gallery.canvas.entries) == 1
        # Cheap status is neutral "checking" (not yet hashed)
        assert state.save_status(entry).status == "checking"

        hash_release.set()
        for _ in range(50):
            QTest.qWait(20)
            if state.save_status(entry).status == "unchanged":
                break

        assert state.save_status(entry).status == "unchanged"
    finally:
        hash_release.set()
        window.close()


def test_stale_hash_cannot_overwrite_new_device(
    qt_app, tmp_path: Path, monkeypatch
):
    first = tmp_path / "first"
    second = tmp_path / "second"
    save1 = first / "save1"
    save2 = second / "save2"
    save1.mkdir(parents=True)
    save2.mkdir(parents=True)
    (save1 / "data.bin").write_bytes(b"data1")
    (save2 / "data.bin").write_bytes(b"data2")
    lib = tmp_path / "lib"

    entry1 = SaveEntry("switch", "demo", "Game 1", str(save1), "0100000000000001")
    entry2 = SaveEntry("switch", "demo", "Game 2", str(save2), "0100000000000002")
    backup_save(entry1, lib, datetime(2026, 1, 1, 10, 0, 0))
    backup_save(entry2, lib, datetime(2026, 1, 1, 10, 0, 0))

    first_hash_started = threading.Event()
    first_hash_release = threading.Event()

    def mock_hash(path):
        if str(first) in str(path):
            first_hash_started.set()
            first_hash_release.wait(timeout=3)
        return "digest"

    monkeypatch.setattr("vajsave.app_state.hash_tree", mock_hash)

    def scan_fn(path):
        path = Path(path)
        if path == first:
            return ScanResult(str(first), "switch", saves=[entry1])
        return ScanResult(str(second), "switch", saves=[entry2])

    state = AppState(
        provider=FakeVolumeProvider([]),
        scan_fn=scan_fn,
        library_root=lib,
    )
    monkeypatch.setattr(state, "start_watch", lambda *args, **kwargs: None)
    monkeypatch.setattr(state, "stop_watch", lambda *args, **kwargs: None)

    window = VajSaveWindow(state)
    try:
        window._request_mount_scan(first)
        for _ in range(50):
            QTest.qWait(20)
            if first_hash_started.is_set():
                break
        assert first_hash_started.is_set()

        # Switch to second device
        window._request_mount_scan(second)
        first_hash_release.set()

        for _ in range(50):
            QTest.qWait(20)
            if state.current_mount == second and state.current_result is not None and state.current_result.root_path == str(second):
                break

        assert state.current_mount == second
        assert state.current_result.root_path == str(second)
        assert entry1.path not in state._backup_statuses
    finally:
        first_hash_release.set()
        window.close()


def test_library_mode_skips_cover_enrichment(qt_app, qt_state, monkeypatch):
    identity_calls = []
    monkeypatch.setattr(qt_state, "resolve_identities", lambda entries=None: identity_calls.append(True) or [])
    qt_state.set_library_mode(True)
    window = VajSaveWindow(qt_state)
    try:
        window.show()
        QTest.qWait(100)
        assert len(identity_calls) == 0
    finally:
        window.close()


def test_scan_progress_bar_hidden_until_scan_starts(qt_app, qt_state):
    window = VajSaveWindow(qt_state)
    try:
        window.show()
        QTest.qWait(30)
        bar = window.scan_progress
        assert bar.objectName() == "scanProgress"
        assert bar.isHidden()
    finally:
        window.close()


def test_scan_progress_bar_visible_during_slow_scan(qt_app, tmp_path: Path, monkeypatch):
    mount = tmp_path / "slow-device"
    mount.mkdir()
    volume = VolumeInfo("慢速存储卡", mount, True)
    started = threading.Event()
    release = threading.Event()

    def slow_scan(_path):
        started.set()
        release.wait(timeout=3)
        return ScanResult(root_path=str(mount), platform="vita", saves=[])

    state = AppState(
        provider=FakeVolumeProvider([volume]),
        scan_fn=slow_scan,
        library_root=tmp_path / "library",
    )
    monkeypatch.setattr(state, "start_watch", lambda *args, **kwargs: None)
    monkeypatch.setattr(state, "stop_watch", lambda *args, **kwargs: None)
    window = VajSaveWindow(state)
    try:
        window.show()
        window._request_mount_scan(mount)
        for _ in range(40):
            QTest.qWait(20)
            if started.is_set():
                break
        assert started.is_set()
        window._sync_scan_progress()
        assert window.scan_progress.isVisible()
        assert "正在扫描" in window.status_text.text()
    finally:
        release.set()
        window.close()


def test_dock_order_places_gb_gbc_between_nds_and_gba(qt_app, qt_state):
    window = VajSaveWindow(qt_state)
    try:
        keys = list(window.dock.buttons)
        assert keys.index("nds") < keys.index("gb") < keys.index("gbc") < keys.index("gba")
        assert keys == list(PLATFORM_ORDER)
    finally:
        window.close()


def test_qt_help_names_plan_topics(qt_app, qt_state):
    from vajsave.qt_dialogs import HELP_TEXT

    assert "不会写入掌机" in HELP_TEXT
    assert "GB" in HELP_TEXT and "GBC" in HELP_TEXT
    assert "ZIP" in HELP_TEXT
    assert "删除单个版本" in HELP_TEXT
    assert "FTP" in HELP_TEXT
    assert "记住密码" in HELP_TEXT
    assert "卷序列号" in HELP_TEXT
    assert "插入后自动备份默认关闭" in HELP_TEXT


def test_drawer_omits_dead_view_all_link(qt_app, qt_state):
    window = VajSaveWindow(qt_state)
    try:
        labels = [widget.text() for widget in window.drawer.findChildren(QLabel)]
        assert "查看全部" not in labels
        assert window.drawer.delete_version.text() == "删除此版本"
        assert window.drawer.star.objectName() == "starButton"
    finally:
        window.close()


def test_topbar_exposes_starred_filter_and_backup_updated(qt_app, qt_state):
    window = VajSaveWindow(qt_state)
    try:
        assert window.starred_only.text() == "只看收藏"
        assert window.backup_updated.text() == "备份有更新"
        assert window.cancel_job.text() == "取消"
        assert window.cancel_job.isHidden()
        assert not hasattr(window, "help")
        topbar = window.findChild(QFrame, "topbar")
        topbar_button_texts = [btn.text() for btn in topbar.findChildren(QPushButton)]
        assert "帮助" not in topbar_button_texts
        window.starred_only.click()
        assert qt_state.starred_only is True
    finally:
        window.close()


def test_dock_omits_moved_actions(qt_app, qt_state):
    window = VajSaveWindow(qt_state)
    try:
        dock_texts = [btn.text() for btn in window.dock.findChildren(QToolButton)]
        assert "添加设备" not in dock_texts
        assert "其他设备" not in dock_texts
        assert "FTP 拉取" not in dock_texts
        assert "刷新设备" in dock_texts
        assert "本地存档" in dock_texts
    finally:
        window.close()


def test_dock_import_zip_visible_only_in_library_mode(qt_app, qt_state):
    window = VajSaveWindow(qt_state)
    try:
        assert window.dock.import_zip.isHidden()
        qt_state.set_library_mode(True)
        window.refresh_all()
        assert not window.dock.import_zip.isHidden()
        assert window.dock.import_zip.text() == "导入 ZIP"
    finally:
        window.close()


def test_main_settings_exposes_auto_backup_gb_gbc_and_browse(qt_app, qt_state):
    window = VajSaveWindow(qt_state)
    found = {}

    def inspect_dialog():
        dialog = QApplication.activeModalWidget()
        found["auto"] = dialog.findChild(QCheckBox, "autoBackupOnInsert")
        found["gb"] = dialog.findChild(QLineEdit, "gbRomDirectory")
        found["gbc"] = dialog.findChild(QLineEdit, "gbcRomDirectory")
        found["add_dev"] = dialog.findChild(QPushButton, "addDeviceBtn")
        found["choose_dev"] = dialog.findChild(QPushButton, "chooseDeviceBtn")
        found["ftp"] = dialog.findChild(QPushButton, "ftpBtn")
        found["browse"] = [
            button for button in dialog.findChildren(QPushButton) if button.text() == "浏览…"
        ]
        dialog.reject()

    try:
        QTimer.singleShot(0, inspect_dialog)
        window._show_settings()
        assert found["auto"] is not None
        assert found["auto"].text() == "插入后自动备份有更新的存档"
        assert found["auto"].isChecked() is False
        assert found["gb"] is not None
        assert found["gbc"] is not None
        assert found["add_dev"] is not None
        assert found["choose_dev"] is not None
        assert found["ftp"] is not None
        assert len(found["browse"]) >= 6
    finally:
        window.close()


def test_ftp_dialog_exposes_remember_password(qt_app, qt_state):
    from vajsave.qt_dialogs import FtpDialog

    dialog = FtpDialog(qt_state)
    box = dialog.findChild(QCheckBox, "rememberFtpPassword")
    assert box is not None
    assert box.text() == "记住密码"
    assert box.isChecked() is False


def test_ambiguous_rom_lists_candidates_and_keeps_manual_bind(qt_app, qt_state, monkeypatch):
    from vajsave.identity import GameIdentity, ambiguous

    entry = qt_state.visible_saves()[0]
    first = GameIdentity(
        identity_key="gba:sha1:aa", platform="gba", title="Apotris", rom_path="/roms/Apotris.gba"
    )
    second = GameIdentity(
        identity_key="gba:sha1:bb",
        platform="gba",
        title="Apotris (Japan)",
        rom_path="/roms/Apotris (Japan).gba",
    )
    monkeypatch.setattr(
        qt_state, "resolve_save_identity", lambda _entry: ambiguous((first, second))
    )
    window = VajSaveWindow(qt_state)
    try:
        window.drawer.set_entry(qt_state, entry)
        assert not window.drawer.candidates.isHidden()
        assert window.drawer.candidates.count() == 2
        assert not window.drawer.bind_candidate.isHidden()
        assert not window.drawer.manual_rom.isHidden()
        assert window.drawer.candidates.currentRow() == 0
    finally:
        window.close()


def test_restore_dialog_starts_at_suggested_dir(qt_app, qt_state, monkeypatch):
    suggested = Path("/tmp/suggested-restore")
    monkeypatch.setattr(qt_state, "suggested_restore_dir", lambda _entry: suggested)
    captured = {}

    def fake_dir(_parent, _title, directory="", *args, **kwargs):
        captured["directory"] = directory
        return ""

    monkeypatch.setattr(qt_ui.QFileDialog, "getExistingDirectory", fake_dir)
    window = VajSaveWindow(qt_state)
    try:
        window._selected = qt_state.visible_saves()[0]
        window._selected_snapshot = object()
        window._restore()
        assert captured["directory"] == str(suggested)
    finally:
        window.close()


def test_auto_backup_runs_after_insert_hash_when_enabled(
    qt_app, tmp_path: Path, monkeypatch
):
    mount = tmp_path / "card"
    save_path = mount / "save1"
    save_path.mkdir(parents=True)
    (save_path / "data.bin").write_bytes(b"payload")
    entry = SaveEntry(
        platform="switch",
        source_id="demo",
        display_name="Game 1",
        path=str(save_path),
        title_id="0100000000000001",
    )
    scan_res = ScanResult(root_path=str(mount), platform="switch", saves=[entry])
    volume = VolumeInfo("测试掌机", mount, True)
    state = AppState(
        provider=FakeVolumeProvider([volume]),
        scan_fn=lambda _path: scan_res,
        library_root=tmp_path / "library",
    )
    state.refresh_volumes()
    state.set_auto_backup_on_insert(True)
    called = []
    monkeypatch.setattr(state, "backup_updated_saves", lambda token=None: called.append(True) or [])
    monkeypatch.setattr(state, "start_watch", lambda *args, **kwargs: None)
    monkeypatch.setattr(state, "stop_watch", lambda *args, **kwargs: None)
    window = VajSaveWindow(state)
    try:
        window._request_mount_scan(mount, auto=True)
        for _ in range(50):
            QTest.qWait(20)
            if called:
                break
        assert called == [True]
    finally:
        window.close()


def test_auto_backup_skipped_when_default_off(qt_app, tmp_path: Path, monkeypatch):
    mount = tmp_path / "card"
    mount.mkdir()
    volume = VolumeInfo("测试掌机", mount, True)
    state = AppState(
        provider=FakeVolumeProvider([volume]),
        scan_fn=lambda _path: ScanResult(root_path=str(mount), platform="switch", saves=[]),
        library_root=tmp_path / "library",
    )
    state.refresh_volumes()
    called = []
    monkeypatch.setattr(state, "backup_updated_saves", lambda token=None: called.append(True) or [])
    monkeypatch.setattr(state, "start_watch", lambda *args, **kwargs: None)
    monkeypatch.setattr(state, "stop_watch", lambda *args, **kwargs: None)
    window = VajSaveWindow(state)
    try:
        window._request_mount_scan(mount, auto=True)
        for _ in range(30):
            QTest.qWait(20)
            if state.current_result is not None and window._device_scan_target is None:
                break
        QTest.qWait(40)
        assert called == []
        assert state.auto_backup_on_insert is False
    finally:
        window.close()



# --- task-3e38b484: dirty-rect painting, LRU, async detail/FTP, empty states --


def test_gallery_paint_only_loads_cases_inside_dirty_rect(qt_app, qt_state, monkeypatch):
    canvas = GalleryCanvas(qt_state)
    canvas.resize(400, 700)
    qt_state.set_platform_filter("all")
    entries = list(qt_state.visible_saves())
    canvas.set_entries(entries)
    assert canvas.columns == 1

    loaded = []
    original_load = canvas._load_pixmap

    def load_spy(entry, *args, **kwargs):
        loaded.append(entry.path)
        return original_load(entry, *args, **kwargs)

    monkeypatch.setattr(canvas, "_load_pixmap", load_spy)
    canvas.paintEvent(QPaintEvent(QRect(0, 0, 400, 360)))

    # Only the first shelf row is dirty; rows below must never resolve or load.
    assert loaded == [entries[0].path]
    assert entries[1].path not in canvas._cover_paths


def test_gallery_pixmap_cache_is_bounded_lru(qt_app, qt_state, tmp_path: Path):
    cover = tmp_path / "cover.png"
    image = QPixmap(40, 60)
    image.fill(Qt.GlobalColor.blue)
    assert image.save(str(cover), "PNG")

    canvas = GalleryCanvas(qt_state)
    entry = qt_state.visible_saves()[0]
    canvas.set_cover_path(entry.path, str(cover))
    for index in range(300):
        canvas._load_pixmap(entry, QRectF(0, 0, 20 + index, 40))

    assert len(canvas._pixmaps) == canvas.PIXMAP_CACHE_SIZE
    widths = {key[1] for key in canvas._pixmaps}
    assert 20 + 299 in widths or 20 + 298 in widths
    assert 20 not in widths


def test_detail_set_entry_never_rglobs_on_ui_thread(qt_app, qt_state, monkeypatch):
    entry = qt_state.visible_saves()[0]
    main_calls = []
    original_rglob = Path.rglob

    def counting_rglob(self, *args, **kwargs):
        if threading.current_thread() is threading.main_thread():
            main_calls.append(str(self))
        return original_rglob(self, *args, **kwargs)

    monkeypatch.setattr(Path, "rglob", counting_rglob)
    window = VajSaveWindow(qt_state)
    try:
        versions = window.drawer.set_entry(qt_state, entry)
        assert main_calls == []
        assert window.drawer.fields["size"].text() in ("…", "—") or window.drawer.fields["size"].text().endswith(("B", "KB", "MB"))
        assert versions is not None
    finally:
        window.close()


def test_detail_size_is_filled_in_background(qt_app, qt_state):
    entry = qt_state.visible_saves()[0]
    expected = sum(
        child.stat().st_size for child in Path(entry.path).rglob("*") if child.is_file()
    )
    window = VajSaveWindow(qt_state)
    try:
        window.drawer.set_entry(qt_state, entry)
        for _ in range(50):
            QTest.qWait(20)
            if window.drawer.fields["size"].text() != "…":
                break
        assert window.drawer.fields["size"].text() == DetailDrawer._format_bytes(expected)
    finally:
        window.close()


def test_ftp_pull_is_submitted_to_device_loader(qt_app, qt_state, monkeypatch):
    window = VajSaveWindow(qt_state)
    try:
        class FakeFtpDialog:
            def __init__(self, *args, **kwargs):
                pass

            def exec(self):
                return QDialog.DialogCode.Accepted

            def configure(self):
                pass

        monkeypatch.setattr(qt_ui, "FtpDialog", FakeFtpDialog)
        pulled = []
        monkeypatch.setattr(qt_state, "pull_ftp_saves", lambda token=None: pulled.append(True))
        submitted = []
        monkeypatch.setattr(
            window._device_loader,
            "submit",
            lambda key, task, callback: submitted.append((key, task)) or True,
        )
        window._show_ftp()
        assert submitted and submitted[0][0] == ("ftp-pull",)
        assert pulled == []
    finally:
        window.close()


def test_gallery_empty_state_distinguishes_reasons(qt_app, qt_state, tmp_path: Path):
    from vajsave.platforms.common import ScanProgress

    canvas = GalleryCanvas(qt_state)
    # Search / filter matched nothing.
    qt_state.set_search_query("no-such-game-xyz")
    canvas.set_entries(qt_state.visible_saves())
    assert "没有符合条件" in canvas._empty_message()
    qt_state.set_search_query("")
    qt_state.set_platform_filter("all")

    # A scan is in flight.
    qt_state.report_scan_progress(ScanProgress(message="正在扫描…"))
    assert "正在扫描" in canvas._empty_message()
    qt_state.clear_scan_progress()

    # Nothing connected yet.
    idle = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "library")
    idle_canvas = GalleryCanvas(idle)
    assert "连接掌机" in idle_canvas._empty_message()


def test_show_drawer_keeps_gallery_columns_and_reserved_width(qt_app, qt_state):
    window = VajSaveWindow(qt_state)
    try:
        window.show()
        QTest.qWait(60)
        canvas = window.gallery.canvas
        columns_before = canvas.columns
        canvas.selected = {0}
        canvas.selection_changed.emit(canvas.selected_entries())
        QTest.qWait(240)
        assert canvas.columns == columns_before
        assert canvas.reserved_right == 0
        window.hide_drawer()
        QTest.qWait(240)
        assert canvas.columns == columns_before
        assert canvas.reserved_right == 0
    finally:
        window.close()


def test_search_does_not_bump_generation_or_reenrich(qt_app, qt_state, monkeypatch):
    window = VajSaveWindow(qt_state)
    try:
        generation = window._list_generation
        submits = []
        monkeypatch.setattr(
            window._artwork_loader,
            "submit",
            lambda key, task, callback: submits.append(key) or True,
        )
        window.search.setText("游戏 1")
        assert window._list_generation == generation
        assert all(not (isinstance(key, tuple) and key and key[0] == "identity") for key in submits)
        assert submits == []
    finally:
        window.close()


def test_export_and_import_zip_run_on_device_loader(qt_app, qt_state, monkeypatch, tmp_path: Path):
    window = VajSaveWindow(qt_state)
    try:
        submitted = []
        monkeypatch.setattr(
            window._device_loader,
            "submit",
            lambda key, task, callback: submitted.append(key) or True,
        )

        class _Snapshot:
            id = "snap-1"
            created_at = "2026-01-01T00:00:00"

            def absolute_path(self, library_root):
                return Path(library_root) / "snap-1"

        window._selected = qt_state.visible_saves()[0]
        window._selected_snapshot = _Snapshot()
        monkeypatch.setattr(
            qt_ui.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(tmp_path / "out.zip"), ""),
        )
        window._export()
        assert submitted == [("export", "snap-1", str(tmp_path / "out.zip"))]
    finally:
        window.close()
    # The first submit never ran (stubbed), so release the shared job slot.
    qt_state._job_slot.end()

    window = VajSaveWindow(qt_state)
    try:
        submitted = []
        monkeypatch.setattr(
            window._device_loader,
            "submit",
            lambda key, task, callback: submitted.append(key) or True,
        )
        zip_path = tmp_path / "import.zip"
        zip_path.write_bytes(b"PK\x03\x04")
        monkeypatch.setattr(
            qt_ui.QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(zip_path), "")
        )
        monkeypatch.setattr(qt_ui, "zip_has_manifest", lambda path: True)
        window._import_zip()
        assert submitted == [("import-zip", str(zip_path))]
    finally:
        window.close()

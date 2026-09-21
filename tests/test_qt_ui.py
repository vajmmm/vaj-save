"""PySide6 运行时的关键布局与交互回归测试。"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QLineEdit, QPushButton

from vajsave.app_state import AppState
from vajsave.artwork import ArtworkResolution, PLACEHOLDER, SOURCE_DOWNLOADED
from vajsave.library import backup_save
from vajsave.metadata import GameMetadata
from vajsave.models import SaveEntry, ScanResult, VolumeInfo
from vajsave.qt_ui import (
    DRAWER_WIDTH,
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


def test_platform_buttons_only_show_the_all_label(qt_app):
    all_button = PlatformButton("all")
    psp_button = PlatformButton("psp")

    assert all_button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextUnderIcon
    assert psp_button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert psp_button.toolTip() == "PSP"
    assert psp_button.accessibleName() == "PSP"


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


def test_gallery_case_geometry_stays_portrait_for_supported_platforms(qt_app, qt_state):
    canvas = GalleryCanvas(qt_state)
    for platform in ("switch", "psp", "vita", "3ds", "nds", "gba"):
        entry = SaveEntry(platform=platform, source_id="test", display_name=platform, path=platform)
        width, height = canvas._case_size(entry)
        assert width <= height, platform


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
        # Cheap status is "changed" (not yet unchanged)
        assert state.save_status(entry).status == "changed"

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
        window.starred_only.click()
        assert qt_state.starred_only is True
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


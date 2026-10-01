"""Tests for LogViewerDialog UI and logging integration."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from vajsave.app_state import AppState
from vajsave.log_store import LogRecordEntry, LogStore
from vajsave.models import SaveEntry, ScanResult, VolumeInfo
from vajsave.qt_logs import LogViewerDialog
from vajsave.qt_ui import VajSaveWindow
from vajsave.volume import FakeVolumeProvider


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def test_log_viewer_dialog_filtering_and_actions(qt_app):
    store = LogStore()
    store.add_record(LogRecordEntry(1.0, "10:00:01", "INFO", "scan", "开始扫描路径 E:\\", "vajsave.scanner"))
    store.add_record(LogRecordEntry(2.0, "10:00:02", "INFO", "cover", "正在下载 Zelda 封面", "vajsave.artwork.service"))
    store.add_record(LogRecordEntry(3.0, "10:00:03", "WARNING", "scan", "路径访问受限", "vajsave.scanner"))

    dialog = LogViewerDialog(store=store)
    try:
        assert "共 3 条日志" in dialog.lbl_count.text()
        assert "Zelda" in dialog.text_browser.toPlainText()

        # Filter by scan
        dialog.btn_scan.click()
        assert dialog._current_category == "scan"
        assert "共 2 条日志" in dialog.lbl_count.text()
        assert "Zelda" not in dialog.text_browser.toPlainText()
        assert "开始扫描路径" in dialog.text_browser.toPlainText()

        # Filter by cover
        dialog.btn_cover.click()
        assert dialog._current_category == "cover"
        assert "共 1 条日志" in dialog.lbl_count.text()
        assert "Zelda" in dialog.text_browser.toPlainText()

        # Back to all
        dialog.btn_all.click()
        assert "共 3 条日志" in dialog.lbl_count.text()

        # Search filter
        dialog.search_input.setText("受限")
        assert "共 1 条日志" in dialog.lbl_count.text()
        assert "路径访问受限" in dialog.text_browser.toPlainText()

        # Live log streaming
        dialog.search_input.clear()
        new_entry = LogRecordEntry(4.0, "10:00:04", "INFO", "cover", "新封面下载成功", "vajsave.artwork.service")
        store.add_record(new_entry)
        assert "新封面下载成功" in dialog.text_browser.toPlainText()
        assert "共 4 条日志" in dialog.lbl_count.text()

        # Copy action
        dialog.btn_copy.click()
        clipboard_text = QApplication.clipboard().text()
        assert "新封面下载成功" in clipboard_text

        # Clear action
        dialog.btn_clear.click()
        assert "共 0 条日志" in dialog.lbl_count.text()
        assert dialog.text_browser.toPlainText().strip() == ""
    finally:
        dialog.close()


def test_vaj_save_window_log_button(qt_app, tmp_path: Path, monkeypatch):
    mount = tmp_path / "device"
    mount.mkdir()
    result = ScanResult(root_path=str(mount), platform="switch", saves=[])
    volume = VolumeInfo("测试掌机", mount, True)
    state = AppState(
        provider=FakeVolumeProvider([volume]),
        scan_fn=lambda _path: result,
        library_root=tmp_path / "library",
    )
    monkeypatch.setattr(state, "start_watch", lambda *args, **kwargs: None)
    monkeypatch.setattr(state, "stop_watch", lambda *args, **kwargs: None)

    window = VajSaveWindow(state)
    try:
        assert hasattr(window, "log_button")
        assert window.log_button.text() == "运行日志"
        assert window._log_dialog is None

        # Click to open logs
        window.log_button.click()
        assert window._log_dialog is not None
        assert isinstance(window._log_dialog, LogViewerDialog)
        assert window._log_dialog.isVisible()
    finally:
        if window._log_dialog:
            window._log_dialog.close()
        window.close()

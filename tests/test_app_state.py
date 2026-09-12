import json
import os
import queue
import time
from datetime import datetime
from pathlib import Path
import pytest

from vajsave.app_state import AppState
from vajsave.artwork.llm_choice import (
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_PROTOCOL,
    PROTOCOL_ANTHROPIC,
    PROTOCOL_OPENAI,
)
from vajsave.library import backup_save
from vajsave.models import SaveEntry, ScanResult, VolumeInfo
from vajsave.volume import FakeVolumeProvider
from vajsave.backend import FakeStorageBackend
from conftest import build_sfo, checkpoint_ftp_tree, fake_client_factory


@pytest.fixture(scope="module")
def tk_root():
    """A single Tk root shared by every UI test in this module.

    macOS Tk cannot reliably tear down and recreate its interpreter inside one
    process: calling ``tk.Tk()`` again after ``root.destroy()`` leaves the second
    root's ``update()`` spinning forever in the Cocoa event loop. Sharing one root
    keeps the UI tests hermetic without that hang.
    """
    try:
        import tkinter as tk

        root = tk.Tk()
    except Exception:
        pytest.skip("Tkinter display not available")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


def _dispose_app(app, root) -> None:
    """Stop an app's background work and drop its widgets, keeping `root` alive."""
    try:
        app._stop_background()
    except Exception:
        pass
    for child in list(root.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass


def test_app_state_init():
    state = AppState()
    assert state.provider is not None
    assert state.volumes == []
    assert state.current_mount is None
    assert state.current_result is None
    assert state.is_watching is False
    assert state.warnings == []
    assert isinstance(state.event_queue, queue.Queue)


def test_app_state_refresh_volumes(tmp_path: Path):
    vol1 = VolumeInfo(name="USB_1", mount_point=tmp_path / "usb1")
    vol2 = VolumeInfo(name="USB_2", mount_point=tmp_path / "usb2")
    provider = FakeVolumeProvider([vol1, vol2])

    state = AppState(provider=provider)
    vols = state.refresh_volumes()
    assert len(vols) == 2
    assert vols[0].name == "USB_1"
    assert vols[1].name == "USB_2"
    assert state.volumes == vols


def test_app_state_select_mount_psp(tmp_path: Path, psp_sfo_bytes: bytes):
    psp_root = tmp_path / "PSP_VOL"
    save_dir = psp_root / "PSP" / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    (save_dir / "DATA.BIN").write_bytes(b"DATA")

    vol = VolumeInfo(name="PSP_VOL", mount_point=psp_root)
    state = AppState(provider=FakeVolumeProvider([vol]))
    state.refresh_volumes()

    res = state.select_mount(psp_root)
    assert res is not None
    assert state.current_mount == psp_root
    assert state.current_result == res
    assert res.platform == "psp"
    assert len(res.saves) == 1
    assert res.saves[0].display_name == "Monster Hunter Portable 3rd"
    assert res.saves[0].title_id == "ULJM05800"
    assert "PSP" in state.status_text
    assert "[只读]" in state.status_text


def test_app_state_select_mount_vita_adrenaline(tmp_path: Path, vita_sfo_bytes: bytes, psp_sfo_bytes: bytes):
    vita_root = tmp_path / "VITA_VOL"
    # Vita Save Manager export
    v_save = vita_root / "data" / "savegames" / "PCSE00120" / "SLOT0"
    v_save.mkdir(parents=True)
    (v_save / "sce_sys").mkdir()
    (v_save / "sce_sys" / "param.sfo").write_bytes(vita_sfo_bytes)

    # Adrenaline PSP partition
    psp_save = vita_root / "pspemu" / "PSP" / "SAVEDATA" / "ULJM05800"
    psp_save.mkdir(parents=True)
    (psp_save / "PARAM.SFO").write_bytes(psp_sfo_bytes)

    state = AppState()
    res = state.select_mount(vita_root)

    assert res is not None
    assert len(res.sources) == 2
    source_ids = {s.source_id for s in res.sources}
    assert "vita_exported" in source_ids
    assert "psp" in source_ids
    assert len(res.saves) == 2
    assert "来源: 2" in state.status_text or "来源: 2 个" in state.status_text


def test_app_state_select_mount_3ds_encrypted(tmp_path: Path):
    n3ds_root = tmp_path / "3DS_ENCRYPTED"
    (n3ds_root / "Nintendo 3DS" / "id0" / "id1").mkdir(parents=True)
    (n3ds_root / "Nintendo 3DS" / "id0" / "id1" / "title.db").write_bytes(b"encrypted")

    state = AppState()
    res = state.select_mount(n3ds_root)

    assert res is not None
    assert res.platform == "3ds"
    assert len(res.saves) == 0
    assert "加密" in state.status_text
    assert "不可直接管理" in state.status_text


def test_app_state_select_mount_unknown(tmp_path: Path):
    empty_vol = tmp_path / "EMPTY_USB"
    empty_vol.mkdir()
    (empty_vol / "Documents").mkdir()
    (empty_vol / "Documents" / "file.txt").write_text("hello")

    state = AppState()
    res = state.select_mount(empty_vol)

    assert res is not None
    assert res.platform == "unknown"
    assert len(res.saves) == 0
    assert "未知" in state.status_text or "unknown" in state.status_text.lower()


def test_app_state_select_nonexistent_path(tmp_path: Path):
    nonexistent = tmp_path / "NONEXISTENT_DIR"
    state = AppState()
    res = state.select_mount(nonexistent)

    assert res is not None
    assert len(state.warnings) > 0
    assert "不存在" in state.status_text or "不可读" in state.status_text


def test_app_state_select_custom_path(tmp_path: Path, psp_sfo_bytes: bytes):
    custom_dir = tmp_path / "CUSTOM_FOLDER"
    psp_dir = custom_dir / "PSP" / "SAVEDATA" / "ULJM05800"
    psp_dir.mkdir(parents=True)
    (psp_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)

    state = AppState()
    res = state.select_custom_path(custom_dir)

    assert res is not None
    assert state.current_mount == custom_dir
    assert len(res.saves) == 1
    assert any(v.mount_point == custom_dir for v in state.volumes)


def test_app_state_watch_appeared_auto_select(tmp_path: Path, psp_sfo_bytes: bytes):
    vol_dir = tmp_path / "AUTO_PSP"
    psp_dir = vol_dir / "PSP" / "SAVEDATA" / "ULJM05800"
    psp_dir.mkdir(parents=True)
    (psp_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)

    vol = VolumeInfo(name="AUTO_PSP", mount_point=vol_dir)
    state = AppState()
    assert state.current_mount is None

    state.apply_watch_event("appeared", vol)
    assert state.current_mount == vol_dir
    assert state.current_result is not None
    assert len(state.current_result.saves) == 1
    assert vol in state.volumes


def test_app_state_watch_appeared_when_already_selected(tmp_path: Path, psp_sfo_bytes: bytes):
    vol1_dir = tmp_path / "VOL1"
    psp_dir = vol1_dir / "PSP" / "SAVEDATA" / "ULJM05800"
    psp_dir.mkdir(parents=True)
    (psp_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    vol1 = VolumeInfo(name="VOL1", mount_point=vol1_dir)

    vol2_dir = tmp_path / "VOL2"
    vol2_dir.mkdir()
    vol2 = VolumeInfo(name="VOL2", mount_point=vol2_dir)

    state = AppState()
    state.select_mount(vol1_dir)
    assert state.current_mount == vol1_dir

    # When vol2 appears, state should keep vol1 selected
    state.apply_watch_event("appeared", vol2)
    assert state.current_mount == vol1_dir
    assert len(state.volumes) >= 1
    assert any(v.mount_point == vol2_dir for v in state.volumes)


def test_app_state_watch_disappeared_current(tmp_path: Path):
    vol_dir = tmp_path / "VANISHING_VOL"
    vol_dir.mkdir()
    vol = VolumeInfo(name="VANISHING_VOL", mount_point=vol_dir)

    state = AppState(provider=FakeVolumeProvider([vol]))
    state.refresh_volumes()
    state.select_mount(vol_dir)
    assert state.current_result is not None

    state.apply_watch_event("disappeared", vol)
    assert state.current_mount is None
    assert state.current_result is None
    assert "已卸载" in state.status_text or "已拔出" in state.status_text
    assert vol not in state.volumes


def test_app_state_watch_disappeared_other(tmp_path: Path, psp_sfo_bytes: bytes):
    vol1_dir = tmp_path / "VOL1"
    psp_dir = vol1_dir / "PSP" / "SAVEDATA" / "ULJM05800"
    psp_dir.mkdir(parents=True)
    (psp_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    vol1 = VolumeInfo(name="VOL1", mount_point=vol1_dir)

    vol2_dir = tmp_path / "VOL2"
    vol2_dir.mkdir()
    vol2 = VolumeInfo(name="VOL2", mount_point=vol2_dir)

    state = AppState(provider=FakeVolumeProvider([vol1, vol2]))
    state.refresh_volumes()
    state.select_mount(vol1_dir)

    orig_result = state.current_result
    state.apply_watch_event("disappeared", vol2)

    assert state.current_mount == vol1_dir
    assert state.current_result == orig_result
    assert not any(v.mount_point == vol2_dir for v in state.volumes)


def test_app_state_drain_events(tmp_path: Path, psp_sfo_bytes: bytes):
    vol1_dir = tmp_path / "VOL1"
    psp_dir = vol1_dir / "PSP" / "SAVEDATA" / "ULJM05800"
    psp_dir.mkdir(parents=True)
    (psp_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    vol1 = VolumeInfo(name="VOL1", mount_point=vol1_dir)

    state = AppState()
    state.event_queue.put(("appeared", vol1))

    processed = state.drain_events()
    assert processed == 1
    assert state.current_mount == vol1_dir
    assert state.current_result is not None

    # Disappear via queue
    state.event_queue.put(("disappeared", vol1))
    processed2 = state.drain_events()
    assert processed2 == 1
    assert state.current_mount is None
    assert state.current_result is None


def test_app_state_watch_thread_lifecycle(tmp_path: Path):
    vol1 = VolumeInfo(name="DYNAMIC_VOL", mount_point=tmp_path / "dyn")
    provider = FakeVolumeProvider([vol1])
    state = AppState(provider=provider)

    assert not state.is_watching
    state.start_watch(interval=0.05)
    assert state.is_watching
    assert state._watch_thread is not None
    assert state._watch_thread.is_alive()

    # Calling start_watch again should be a no-op
    state.start_watch(interval=0.05)

    time.sleep(0.1)
    processed = state.drain_events()
    assert processed >= 1
    assert any(v.name == "DYNAMIC_VOL" for v in state.volumes)

    state.stop_watch(timeout=0.5)
    assert not state.is_watching
    assert state._watch_thread is None

    # Idempotent stop
    state.stop_watch()
    assert not state.is_watching


def test_app_state_readonly_invariant(tmp_path: Path, psp_sfo_bytes: bytes):
    psp_dir = tmp_path / "PSP_VOL" / "PSP" / "SAVEDATA" / "ULJM05800"
    psp_dir.mkdir(parents=True)
    sfo_file = psp_dir / "PARAM.SFO"
    sfo_file.write_bytes(psp_sfo_bytes)
    bin_file = psp_dir / "DATA.BIN"
    bin_file.write_bytes(b"SAVE_PAYLOAD_123456789")

    # Record initial mtimes and hashes/contents
    initial_sfo_mtime = sfo_file.stat().st_mtime_ns
    initial_bin_mtime = bin_file.stat().st_mtime_ns
    initial_bin_bytes = bin_file.read_bytes()

    state = AppState()
    state.select_mount(tmp_path / "PSP_VOL")

    # Verify no modification
    assert sfo_file.stat().st_mtime_ns == initial_sfo_mtime
    assert bin_file.stat().st_mtime_ns == initial_bin_mtime
    assert bin_file.read_bytes() == initial_bin_bytes


def test_app_state_with_fake_backend(tmp_path: Path):
    root1 = tmp_path / "ROOT1"
    root1.mkdir()
    backend = FakeStorageBackend([root1])
    state = AppState(backend=backend)
    assert state.backend == backend


def test_app_state_refresh_volumes_error():
    class FailingProvider:
        def list_volumes(self):
            raise OSError("Volume scan failed")

    state = AppState(provider=FailingProvider())
    vols = state.refresh_volumes()
    assert vols == []
    assert len(state.warnings) > 0
    assert "刷新卷列表失败" in state.warnings[0]


def test_app_state_scan_fn_exception(tmp_path: Path):
    def failing_scan(path):
        raise RuntimeError("Disk read error")

    state = AppState(scan_fn=failing_scan)
    res = state.select_mount(tmp_path)
    assert res is not None
    assert len(res.warnings) > 0
    assert "扫描异常" in res.warnings[0]


def test_app_state_select_custom_path_already_present(tmp_path: Path):
    custom_dir = tmp_path / "CUSTOM_DIR"
    custom_dir.mkdir()
    vol = VolumeInfo(name="MY_CUSTOM", mount_point=custom_dir)
    state = AppState(provider=FakeVolumeProvider([vol]))
    state.refresh_volumes()
    assert len(state.volumes) == 1

    state.select_custom_path(custom_dir)
    # Should not duplicate the volume entry
    assert len(state.volumes) == 1


def test_app_state_watch_appeared_update_existing_volume(tmp_path: Path):
    vol_dir = tmp_path / "VOL"
    vol_dir.mkdir()
    vol1 = VolumeInfo(name="VOL_OLD", mount_point=vol_dir)
    vol2 = VolumeInfo(name="VOL_NEW", mount_point=vol_dir)

    state = AppState(provider=FakeVolumeProvider([vol1]))
    state.refresh_volumes()
    assert state.volumes[0].name == "VOL_OLD"

    state.apply_watch_event("appeared", vol2)
    assert state.volumes[0].name == "VOL_NEW"


def test_open_in_file_manager_nonexistent(tmp_path: Path):
    from vajsave.app_ui import open_in_file_manager
    ok, msg = open_in_file_manager(tmp_path / "nonexistent")
    assert not ok
    assert "不存在" in msg


def test_open_in_file_manager_darwin(tmp_path: Path, monkeypatch):
    import sys
    import subprocess
    from vajsave.app_ui import open_in_file_manager

    monkeypatch.setattr(sys, "platform", "darwin")
    called_cmd = []

    def mock_run(cmd, check=True):
        called_cmd.append(cmd)

    monkeypatch.setattr(subprocess, "run", mock_run)

    test_file = tmp_path / "test.txt"
    test_file.write_text("dummy")

    ok, msg = open_in_file_manager(test_file)
    assert ok
    assert "已在文件管理器中打开" in msg
    assert called_cmd == [["open", "-R", str(test_file)]]


def test_open_in_file_manager_win32(tmp_path: Path, monkeypatch):
    import sys
    import subprocess
    from vajsave.app_ui import open_in_file_manager

    monkeypatch.setattr(sys, "platform", "win32")
    called_cmd = []

    def mock_run(cmd, check=True):
        called_cmd.append(cmd)

    monkeypatch.setattr(subprocess, "run", mock_run)

    test_file = tmp_path / "test.txt"
    test_file.write_text("dummy")

    ok, msg = open_in_file_manager(test_file)
    assert ok
    assert called_cmd == [["explorer", f"/select,{test_file}"]]


def test_open_in_file_manager_linux(tmp_path: Path, monkeypatch):
    import sys
    import subprocess
    from vajsave.app_ui import open_in_file_manager

    monkeypatch.setattr(sys, "platform", "linux")
    called_cmd = []

    def mock_run(cmd, check=True):
        called_cmd.append(cmd)

    monkeypatch.setattr(subprocess, "run", mock_run)

    test_file = tmp_path / "test.txt"
    test_file.write_text("dummy")

    ok, msg = open_in_file_manager(test_file)
    assert ok
    assert called_cmd == [["xdg-open", str(tmp_path)]]


def test_open_in_file_manager_failure(tmp_path: Path, monkeypatch):
    import subprocess
    from vajsave.app_ui import open_in_file_manager

    def mock_run(cmd, check=True):
        raise OSError("Permission denied")

    monkeypatch.setattr(subprocess, "run", mock_run)

    test_file = tmp_path / "test.txt"
    test_file.write_text("dummy")

    ok, msg = open_in_file_manager(test_file)
    assert not ok
    assert "失败" in msg


def test_build_app_structure(tk_root):
    from vajsave.app_ui import build_app

    state = AppState(provider=FakeVolumeProvider([]))
    app = build_app(state=state, root=tk_root)
    try:
        assert app.root == tk_root
        assert app.state == state
        app.on_refresh_clicked()
        app.on_watch_toggle()
    finally:
        # Share the module Tk root instead of destroying it: recreating Tk in the
        # same process is what hung the event loop on macOS.
        _dispose_app(app, tk_root)


def _psp_save_tree(root: Path, title_id: str, payload: bytes, psp_sfo_bytes: bytes) -> Path:
    save_dir = root / "PSP" / "SAVEDATA" / title_id
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    (save_dir / "DATA.BIN").write_bytes(payload)
    return save_dir


def test_scan_marks_never_backed_up_as_new(tmp_path: Path, psp_sfo_bytes: bytes):
    root = tmp_path / "PSP_VOL"
    _psp_save_tree(root, "ULJM05800", b"v1", psp_sfo_bytes)
    lib = tmp_path / "lib"
    state = AppState(library_root=lib)
    state.select_mount(root)

    entry = state.all_saves()[0]
    status = state.save_status(entry)
    assert status.status == "new"
    assert status.mtime_stale is False
    assert state.hide_unchanged is False
    assert entry in state.visible_saves()
    assert "新 1" in state.status_text
    assert "有变化 0" in state.status_text
    assert "已备份 0" in state.status_text
    # no progress / update wording
    assert "有更新" not in state.status_text
    assert "进度" not in state.status_text


def test_identical_to_latest_is_unchanged_shown_by_default(tmp_path: Path, psp_sfo_bytes: bytes):
    root = tmp_path / "PSP_VOL"
    save_dir = _psp_save_tree(root, "ULJM05800", b"same", psp_sfo_bytes)
    lib = tmp_path / "lib"
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="Monster Hunter Portable 3rd",
        path=str(save_dir),
        title_id="ULJM05800",
    )
    backup_save(entry, lib, datetime(2026, 1, 1, 10, 0, 0))

    state = AppState(library_root=lib)
    assert state.hide_unchanged is False
    state.select_mount(root)

    scanned = state.all_saves()[0]
    status = state.save_status(scanned)
    assert status.status == "unchanged"
    assert status.mtime_stale is False
    assert status.last_backup_at
    # Default lists every save, unchanged included.
    assert scanned in state.visible_saves()
    assert "已备份 1" in state.status_text
    # The "仅显示有更新" filter then hides the backed-up row.
    state.toggle_hide_unchanged()
    assert state.visible_saves() == []


def test_content_change_marks_changed(tmp_path: Path, psp_sfo_bytes: bytes):
    root = tmp_path / "PSP_VOL"
    save_dir = _psp_save_tree(root, "ULJM05800", b"v1", psp_sfo_bytes)
    lib = tmp_path / "lib"
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="Monster Hunter Portable 3rd",
        path=str(save_dir),
        title_id="ULJM05800",
    )
    backup_save(entry, lib, datetime(2026, 1, 1, 10, 0, 0))
    (save_dir / "DATA.BIN").write_bytes(b"v2")

    state = AppState(library_root=lib)
    state.select_mount(root)
    scanned = state.all_saves()[0]
    assert state.save_status(scanned).status == "changed"
    assert scanned in state.visible_saves()
    assert "有变化 1" in state.status_text


def test_matches_older_snapshot_but_not_latest_is_changed(tmp_path: Path, psp_sfo_bytes: bytes):
    """Only the latest snapshot hash decides unchanged; older match is still changed."""
    root = tmp_path / "PSP_VOL"
    save_dir = _psp_save_tree(root, "ULJM05800", b"old", psp_sfo_bytes)
    lib = tmp_path / "lib"
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="Monster Hunter Portable 3rd",
        path=str(save_dir),
        title_id="ULJM05800",
    )
    backup_save(entry, lib, datetime(2026, 1, 1, 10, 0, 0))
    (save_dir / "DATA.BIN").write_bytes(b"new")
    backup_save(entry, lib, datetime(2026, 1, 2, 10, 0, 0))
    # card rolled back to older content
    (save_dir / "DATA.BIN").write_bytes(b"old")

    state = AppState(library_root=lib)
    state.select_mount(root)
    scanned = state.all_saves()[0]
    status = state.save_status(scanned)
    assert status.status == "changed"
    assert scanned in state.visible_saves()


def test_toggle_updated_only_filter_hides_backed_up(tmp_path: Path, psp_sfo_bytes: bytes):
    root = tmp_path / "PSP_VOL"
    save_dir = _psp_save_tree(root, "ULJM05800", b"same", psp_sfo_bytes)
    lib = tmp_path / "lib"
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="Monster Hunter Portable 3rd",
        path=str(save_dir),
        title_id="ULJM05800",
    )
    backup_save(entry, lib, datetime(2026, 1, 1, 10, 0, 0))

    state = AppState(library_root=lib)
    state.select_mount(root)
    # Default shows the unchanged save.
    assert len(state.visible_saves()) == 1

    toggled = state.toggle_hide_unchanged()
    assert toggled is True  # hide_unchanged now True
    assert state.hide_unchanged is True
    assert state.visible_saves() == []


def test_hash_failure_does_not_break_select_mount(tmp_path: Path, psp_sfo_bytes: bytes, monkeypatch):
    root = tmp_path / "PSP_VOL"
    save_dir = _psp_save_tree(root, "ULJM05800", b"data", psp_sfo_bytes)
    lib = tmp_path / "lib"
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="Monster Hunter Portable 3rd",
        path=str(save_dir),
        title_id="ULJM05800",
    )
    backup_save(entry, lib, datetime(2026, 1, 1, 10, 0, 0))

    def boom(_path):
        raise OSError("permission denied")

    monkeypatch.setattr("vajsave.app_state.hash_tree", boom)
    state = AppState(library_root=lib)
    res = state.select_mount(root)
    assert res is not None
    assert len(res.saves) == 1
    status = state.save_status(res.saves[0])
    assert status.status == "new"
    assert any("hash" in w.lower() or "哈希" in w or "摘要" in w or "失败" in w for w in state.warnings)


def test_select_mount_skips_hash_for_unbacked_saves(tmp_path: Path, psp_sfo_bytes: bytes, monkeypatch):
    root = tmp_path / "PSP_VOL"
    _psp_save_tree(root, "ULJM05800", b"data", psp_sfo_bytes)

    def boom(_path):
        raise AssertionError("unbacked saves must not be hashed")

    monkeypatch.setattr("vajsave.app_state.hash_tree", boom)
    state = AppState(library_root=tmp_path / "lib")
    res = state.select_mount(root)
    assert res.saves
    assert state.save_status(res.saves[0]).status == "new"


def test_import_visible_skips_hidden_unchanged(tmp_path: Path, psp_sfo_bytes: bytes):
    root = tmp_path / "PSP_VOL"
    save_a = _psp_save_tree(root, "ULJM05800", b"a", psp_sfo_bytes)
    save_b = root / "PSP" / "SAVEDATA" / "ULUS12345"
    save_b.mkdir(parents=True)
    # second save: minimal PARAM.SFO so scanner still picks it if possible — inject via scan result instead
    lib = tmp_path / "lib"

    # Pre-backup only ULJM05800 so it becomes unchanged after scan
    entry_a = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="Monster Hunter Portable 3rd",
        path=str(save_a),
        title_id="ULJM05800",
    )
    backup_save(entry_a, lib, datetime(2026, 1, 1, 10, 0, 0))

    # Create a second never-backed-up save tree the scanner will find
    from conftest import build_sfo

    sfo_b = build_sfo(
        {
            "TITLE": "Other Game",
            "SAVEDATA_TITLE": "Other Game",
            "SAVEDATA_DETAIL": "",
            "SAVEDATA_DIRECTORY": "ULUS12345",
            "TITLE_ID": "ULUS12345",
            "CATEGORY": "MS",
            "PARENTAL_LEVEL": 0,
        }
    )
    (save_b / "PARAM.SFO").write_bytes(sfo_b)
    (save_b / "DATA.BIN").write_bytes(b"b-new")

    state = AppState(library_root=lib)
    state.select_mount(root)
    assert state.hide_unchanged is False
    # Enable "仅显示有更新" so unchanged rows drop out of the visible list.
    state.toggle_hide_unchanged()
    assert state.hide_unchanged is True
    visible = state.visible_saves()
    assert len(visible) == 1
    assert visible[0].title_id == "ULUS12345"
    hidden_unchanged = [s for s in state.all_saves() if s.title_id == "ULJM05800"][0]
    assert state.save_status(hidden_unchanged).status == "unchanged"
    assert hidden_unchanged not in visible

    copied = state.import_visible_saves()
    assert len(copied) == 1
    # Hidden unchanged was not part of the backup batch target list.
    assert copied[0].exists()
    assert state.save_status(hidden_unchanged).status == "unchanged"
    # After backup, the previously-new visible row becomes unchanged and hides.
    assert state.visible_saves() == []


def test_mtime_stale_only_when_changed_and_source_older(tmp_path: Path, psp_sfo_bytes: bytes):
    root = tmp_path / "PSP_VOL"
    save_dir = _psp_save_tree(root, "ULJM05800", b"v1", psp_sfo_bytes)
    lib = tmp_path / "lib"
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="Monster Hunter Portable 3rd",
        path=str(save_dir),
        title_id="ULJM05800",
    )
    backup_save(entry, lib, datetime(2026, 6, 1, 12, 0, 0))

    # Change content but set directory mtime earlier than backup
    (save_dir / "DATA.BIN").write_bytes(b"rolled-back")
    old_ts = datetime(2026, 1, 1, 8, 0, 0).timestamp()
    os.utime(save_dir, (old_ts, old_ts))

    state = AppState(library_root=lib)
    state.select_mount(root)
    status = state.save_status(state.all_saves()[0])
    assert status.status == "changed"
    assert status.mtime_stale is True
    assert status.source_mtime
    assert status.last_backup_at


def test_identical_hash_ignores_mtime_jitter(tmp_path: Path, psp_sfo_bytes: bytes):
    root = tmp_path / "PSP_VOL"
    save_dir = _psp_save_tree(root, "ULJM05800", b"same", psp_sfo_bytes)
    lib = tmp_path / "lib"
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="Monster Hunter Portable 3rd",
        path=str(save_dir),
        title_id="ULJM05800",
    )
    backup_save(entry, lib, datetime(2026, 1, 1, 10, 0, 0))

    # FAT/USB mtime chaos: far in the future, must NOT flip classification
    future_ts = datetime(2030, 1, 1, 0, 0, 0).timestamp()
    os.utime(save_dir, (future_ts, future_ts))

    state = AppState(library_root=lib)
    state.select_mount(root)
    status = state.save_status(state.all_saves()[0])
    assert status.status == "unchanged"
    assert status.mtime_stale is False
    # shown by default — mtime must not override list filtering
    assert state.all_saves()[0] in state.visible_saves()
    # "仅显示有更新" still hides it despite the mtime jitter
    state.toggle_hide_unchanged()
    assert state.visible_saves() == []


def test_backup_refreshes_status_to_unchanged(tmp_path: Path, psp_sfo_bytes: bytes):
    root = tmp_path / "PSP_VOL"
    _psp_save_tree(root, "ULJM05800", b"v1", psp_sfo_bytes)
    lib = tmp_path / "lib"
    state = AppState(library_root=lib)
    state.select_mount(root)
    entry = state.visible_saves()[0]
    assert state.save_status(entry).status == "new"

    dest = state.import_save(entry)
    assert dest is not None
    assert state.save_status(entry).status == "unchanged"
    # Default keeps the backed-up row visible; the filter hides it.
    assert entry in state.visible_saves()
    state.toggle_hide_unchanged()
    assert state.visible_saves() == []


def test_import_selected_saves(tmp_path: Path, psp_sfo_bytes: bytes):
    root = tmp_path / "PSP_VOL"
    _psp_save_tree(root, "ULJM05800", b"a", psp_sfo_bytes)
    save_b = root / "PSP" / "SAVEDATA" / "ULUS12345"
    save_b.mkdir(parents=True)
    sfo_b = build_sfo(
        {
            "TITLE": "Other Game",
            "SAVEDATA_TITLE": "Other Game",
            "SAVEDATA_DETAIL": "",
            "SAVEDATA_DIRECTORY": "ULUS12345",
            "TITLE_ID": "ULUS12345",
            "CATEGORY": "MS",
            "PARENTAL_LEVEL": 0,
        }
    )
    (save_b / "PARAM.SFO").write_bytes(sfo_b)
    (save_b / "DATA.BIN").write_bytes(b"b")

    lib = tmp_path / "lib"
    state = AppState(library_root=lib)
    state.select_mount(root)
    # Focus on updated saves so the backup-hidden transition is observable.
    state.toggle_hide_unchanged()
    all_visible = state.visible_saves()
    assert len(all_visible) == 2
    chosen = [all_visible[0]]
    copied = state.import_selected_saves(chosen)
    assert len(copied) == 1
    assert state.save_status(chosen[0]).status == "unchanged"
    # the other remains new/visible
    remaining = state.visible_saves()
    assert len(remaining) == 1
    assert remaining[0].path != chosen[0].path





# --- application config / library root selection ---


def test_app_state_reads_library_root_from_config(tmp_path: Path, monkeypatch):
    from vajsave.library import save_app_config

    cfg = tmp_path / "config.json"
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(cfg))
    configured = tmp_path / "configured-lib"
    save_app_config({"library_root": str(configured)})

    state = AppState()
    assert state.library_root == configured


def test_app_state_falls_back_to_default_library_root(tmp_path: Path, monkeypatch):
    from vajsave.library import default_library_root

    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "missing.json"))
    state = AppState()
    assert state.library_root == default_library_root()


def test_app_state_invalid_config_library_root_falls_back(tmp_path: Path, monkeypatch):
    from vajsave.library import default_library_root

    cfg = tmp_path / "config.json"
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(cfg))
    cfg.write_text(json.dumps({"library_root": 12345}), encoding="utf-8")
    state = AppState()
    assert state.library_root == default_library_root()


def test_set_library_root_persists_and_invalidates_cache(tmp_path: Path, monkeypatch, psp_sfo_bytes: bytes):
    from vajsave.library import load_app_config

    cfg = tmp_path / "config.json"
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(cfg))

    root = tmp_path / "PSP_VOL"
    save_dir = root / "PSP" / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    (save_dir / "DATA.BIN").write_bytes(b"same")

    old_lib = tmp_path / "old_lib"
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="Monster Hunter Portable 3rd",
        path=str(save_dir),
        title_id="ULJM05800",
    )
    backup_save(entry, old_lib, datetime(2026, 1, 1, 10, 0, 0))

    state = AppState(library_root=old_lib)
    state.select_mount(root)
    scanned = state.all_saves()[0]
    assert state.save_status(scanned).status == "unchanged"

    new_lib = tmp_path / "new_lib"
    state.set_library_root(new_lib)
    assert state.library_root == new_lib
    assert load_app_config()["library_root"] == str(new_lib)
    # cache invalidated and recomputed against the new (empty) library
    assert state.save_status(scanned).status == "new"


def test_set_library_root_without_scan(tmp_path: Path, monkeypatch):
    from vajsave.library import load_app_config

    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "config.json"))
    state = AppState(library_root=tmp_path / "before")
    new_lib = tmp_path / "after"
    state.set_library_root(new_lib)
    assert state.library_root == new_lib
    assert load_app_config()["library_root"] == str(new_lib)


def test_set_keep_last_persists_and_accepts_zero(tmp_path: Path):
    from vajsave.library import load_keep_last

    state = AppState(library_root=tmp_path / "lib")
    assert state.set_keep_last(3) == 3
    assert load_keep_last(state.library_root) == 3
    # 0 is a valid value meaning "unlimited".
    assert state.set_keep_last(0) == 0
    assert load_keep_last(state.library_root) == 0
    # Numeric strings from the settings entry are accepted.
    assert state.set_keep_last("5") == 5
    assert load_keep_last(state.library_root) == 5


def test_set_keep_last_rejects_invalid_without_writing(tmp_path: Path):
    from vajsave.library import load_keep_last

    state = AppState(library_root=tmp_path / "lib")
    state.set_keep_last(2)
    for bad in (-1, True, "abc", "", 1.5, None):
        assert state.set_keep_last(bad) is None
    assert load_keep_last(state.library_root) == 2


def test_set_keep_last_does_not_prune_existing_versions(tmp_path: Path):
    from vajsave.library import backup_save, game_key, load_catalog
    from vajsave.models import SaveEntry

    lib = tmp_path / "lib"
    folder = tmp_path / "src" / "save"
    folder.mkdir(parents=True)
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="Game",
        path=str(folder),
        title_id="ULJM05800",
    )
    for i, payload in enumerate((b"a", b"b", b"c")):
        (folder / "save.bin").write_bytes(payload)
        backup_save(entry, lib, datetime(2026, 1, 1, 10, i, 0))
    assert len(load_catalog(lib).games[game_key(entry)].versions) == 3

    state = AppState(library_root=lib)
    assert state.set_keep_last(1) == 1
    # Changing the setting must not immediately delete existing versions.
    assert len(load_catalog(lib).games[game_key(entry)].versions) == 3


# --- app UI structure ---


def test_app_ui_detail_scroll_and_volume_index(tk_root, tmp_path: Path, psp_sfo_bytes: bytes):
    import tkinter as tk

    from vajsave.app_ui import build_app

    vol_dir = tmp_path / "VOL"
    psp_dir = vol_dir / "PSP" / "SAVEDATA" / "ULJM05800"
    psp_dir.mkdir(parents=True)
    (psp_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    (psp_dir / "DATA.BIN").write_bytes(b"data")

    # name deliberately contains the old "  ·  " separator; index mapping must win
    vol = VolumeInfo(name="WEIRD  ·  NAME", mount_point=vol_dir)
    state = AppState(provider=FakeVolumeProvider([vol]), library_root=tmp_path / "lib")
    state.refresh_volumes()
    app = build_app(state=state, root=tk_root)
    try:
        assert hasattr(app, "on_settings_clicked")
        # The right panel is the inspector: the versions Listbox fills the
        # flexible inspector row beneath the action area.
        assert app.actions_frame is not None
        assert set(app.versions_frame.grid_info().get("sticky") or "") == set("nsew")
        assert int(app.versions_frame.grid_rowconfigure(1)["weight"]) == 1

        assert len(app._volumes_index) == 0
        # The device list only shows the current device; ask it to present one.
        app.refresh_volumes_ui(select_path=vol_dir)
        assert len(app._volumes_index) == 1
        tk_root.update_idletasks()
        app.vol_list.selection_clear(0, tk.END)
        app.vol_list.selection_set(0)
        app.on_volume_selected()
        assert state.current_mount == vol_dir

        # The versions Listbox keeps its native wheel scrolling.
        assert app.version_list.bind("<MouseWheel>") == ""
        assert app.version_list.bind("<Button-4>") == ""
    finally:
        _dispose_app(app, tk_root)


def test_settings_dialog_applies_library_root(tk_root, tmp_path: Path, monkeypatch):
    import tkinter as tk

    from vajsave.app_ui import build_app
    from vajsave.library import load_app_config

    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "config.json"))
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        app.on_settings_clicked()
        # update_idletasks lays out widgets without pumping the full Cocoa event loop.
        tk_root.update_idletasks()
        tops = [w for w in tk_root.winfo_children() if isinstance(w, tk.Toplevel)]
        assert tops
        # save path of the dialog
        app._apply_library_root(str(tmp_path / "newlib"))
        assert state.library_root == tmp_path / "newlib"
        assert load_app_config()["library_root"] == str(tmp_path / "newlib")
    finally:
        _dispose_app(app, tk_root)


# --- default device selection ---


def _stub_scan(root):
    """Selection tests exercise ordering, not filesystem scanning."""
    return ScanResult(root_path=str(root), platform="unknown", sources=[], saves=[], warnings=[])


def _drives():
    """C/D/E fixed drives plus a removable USB stick on F, as Windows would report."""
    return [
        VolumeInfo(name="C: 本地磁盘", mount_point=Path("C:\\"), is_removable=False),
        VolumeInfo(name="D: 本地磁盘", mount_point=Path("D:\\"), is_removable=False),
        VolumeInfo(name="E: 本地磁盘", mount_point=Path("E:\\"), is_removable=False),
        VolumeInfo(name="F: KINGSTON", mount_point=Path("F:\\"), is_removable=True),
    ]


def _drive_state(volumes):
    return AppState(provider=FakeVolumeProvider(volumes), scan_fn=_stub_scan)


def test_preferred_volume_prefers_removable_over_fixed():
    state = _drive_state(_drives())
    state.refresh_volumes()
    assert state.preferred_volume().mount_point == Path("F:\\")


def test_no_removable_device_does_not_auto_scan_fixed():
    state = _drive_state(_drives()[:3])  # C/D/E fixed, no removable device
    state.refresh_volumes()
    assert state.preferred_volume() is None
    assert state.ensure_mount_selected() is None
    assert state.current_mount is None


def test_startup_burst_settles_on_removable_drive():
    state = _drive_state([])
    # The watcher reports devices one at a time; the removable F: arrives last.
    for vol in _drives():
        state.apply_watch_event("appeared", vol)
    assert state.current_mount == Path("F:\\")


def test_user_selection_survives_later_removable_arrival():
    state = _drive_state(_drives()[:3])
    state.refresh_volumes()
    state.select_mount(Path("D:\\"))  # explicit user choice
    state.apply_watch_event("appeared", _drives()[3])
    assert state.current_mount == Path("D:\\")


def test_auto_selection_upgrades_to_better_removable():
    state = _drive_state([_drives()[2]])  # only E: fixed: nothing is auto-selected
    state.refresh_volumes()
    assert state.ensure_mount_selected() is None
    assert state.current_mount is None

    state.apply_watch_event("appeared", _drives()[3])  # USB stick on F:
    assert state.current_mount == Path("F:\\")


def test_watch_disappeared_current_does_not_fall_back_to_fixed():
    usb = _drives()[3]
    state = _drive_state([usb])
    state.refresh_volumes()
    assert state.ensure_mount_selected().mount_point == Path("F:\\")

    state.apply_watch_event("disappeared", usb)
    assert state.current_mount is None

    state.apply_watch_event("appeared", _drives()[2])  # E: fixed stays unselected
    assert state.current_mount is None


# --- local library browse mode ----------------------------------------------


def _library_backup(tmp_path, lib, platform, title_id, name, when):
    folder = tmp_path / "src" / platform / title_id
    folder.mkdir(parents=True)
    (folder / "save.bin").write_bytes((title_id + name).encode("utf-8"))
    entry = SaveEntry(
        platform=platform,
        source_id=platform,
        display_name=name,
        path=str(folder),
        title_id=title_id,
    )
    backup_save(entry, lib, when=when)
    return entry


def test_library_mode_lists_cross_platform_games_newest_first(tmp_path):
    lib = tmp_path / "lib"
    _library_backup(tmp_path, lib, "psp", "ULJM05800", "Older PSP", datetime(2024, 1, 1, 10, 0, 0))
    _library_backup(tmp_path, lib, "gba", "AGBE01", "Newer GBA", datetime(2024, 5, 1, 10, 0, 0))
    _library_backup(tmp_path, lib, "nds", "ADME01", "Middle NDS", datetime(2024, 3, 1, 10, 0, 0))

    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    state.set_library_mode(True)

    assert state.library_mode is True
    saves = state.visible_saves()
    assert [s.display_name for s in saves] == ["Newer GBA", "Middle NDS", "Older PSP"]
    assert len({s.extra["library_game_id"] for s in saves}) == 3  # one row per game
    # every library row is already backed up, so the "有更新" filter must not blank it
    state.toggle_hide_unchanged()
    assert [s.display_name for s in state.visible_saves()] == [
        "Newer GBA",
        "Middle NDS",
        "Older PSP",
    ]
    assert state.save_status(saves[0]).status == "unchanged"


def test_library_mode_platform_search_star_note_and_versions(tmp_path):
    lib = tmp_path / "lib"
    _library_backup(tmp_path, lib, "psp", "ULJM05800", "Older PSP", datetime(2024, 1, 1, 10, 0, 0))
    _library_backup(tmp_path, lib, "gba", "AGBE01", "Newer GBA", datetime(2024, 5, 1, 10, 0, 0))

    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    state.set_library_mode(True)
    gba = next(s for s in state.visible_saves() if s.platform == "gba")

    state.set_platform_filter("gba")
    assert [s.platform for s in state.visible_saves()] == ["gba"]
    assert state.platform_counts()["gba"] == 1

    state.set_platform_filter("all")
    state.set_search_query("Newer")
    assert [s.display_name for s in state.visible_saves()] == ["Newer GBA"]
    state.set_search_query("")

    state.toggle_star(gba)
    state.toggle_starred_only()
    assert [s.display_name for s in state.visible_saves()] == ["Newer GBA"]
    state.toggle_starred_only()

    assert len(state.versions_for_entry(gba)) == 1
    state.set_note(gba, "存档备注")
    assert state.game_note(gba) == "存档备注"

    fresh = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    fresh.set_library_mode(True)
    gba2 = next(s for s in fresh.visible_saves() if s.platform == "gba")
    assert fresh.is_starred(gba2)
    assert fresh.game_note(gba2) == "存档备注"


def test_library_mode_backup_does_not_copy_library_into_itself(tmp_path):
    lib = tmp_path / "lib"
    _library_backup(tmp_path, lib, "psp", "ULJM05800", "PSP Game", datetime(2024, 1, 1, 10, 0, 0))
    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    state.set_library_mode(True)
    saves = state.visible_saves()

    before = sorted(str(p) for p in lib.rglob("*") if p.is_file())
    assert state.import_save(saves[0]) is None
    assert state.import_selected_saves(saves) == []
    after = sorted(str(p) for p in lib.rglob("*") if p.is_file())
    assert after == before


def test_switching_source_resets_stale_platform_filter(tmp_path):
    lib = tmp_path / "lib"
    _library_backup(tmp_path, lib, "gba", "AGBE01", "GBA Game", datetime(2024, 1, 1, 10, 0, 0))
    vol_dir = tmp_path / "VOL"
    vol_dir.mkdir()
    vol = VolumeInfo(name="VOL", mount_point=vol_dir, is_removable=True)

    state = AppState(
        provider=FakeVolumeProvider([vol]), scan_fn=_stub_scan, library_root=lib
    )
    state.refresh_volumes()

    state.set_library_mode(True)
    state.set_platform_filter("gba")
    state.select_mount(vol_dir)  # switching back to a device source
    assert state.library_mode is False
    assert state.selected_platform == "all"

    state.set_platform_filter("psp")
    state.set_library_mode(True)  # switching to the library source
    assert state.selected_platform == "all"


def test_library_mode_versions_export_and_restore_use_library_payload(tmp_path):
    lib = tmp_path / "lib"
    _library_backup(tmp_path, lib, "psp", "ULJM05800", "PSP Game", datetime(2024, 1, 1, 10, 0, 0))
    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    state.set_library_mode(True)

    entry = state.visible_saves()[0]
    versions = state.versions_for_entry(entry)
    assert len(versions) == 1
    snapshot = versions[0]

    zip_path = state.export_version_zip(snapshot, tmp_path / "exported")
    assert zip_path is not None and Path(zip_path).is_file()

    restored = state.restore_version(snapshot, tmp_path / "restored")
    assert restored is not None and Path(restored).exists()


def test_library_mode_survives_later_device_events(tmp_path):
    lib = tmp_path / "lib"
    _library_backup(tmp_path, lib, "psp", "ULJM05800", "PSP Game", datetime(2024, 1, 1, 10, 0, 0))
    usb = VolumeInfo(name="F: KINGSTON", mount_point=Path("F:\\"), is_removable=True)

    state = AppState(
        provider=FakeVolumeProvider([usb]), scan_fn=_stub_scan, library_root=lib
    )
    state.refresh_volumes()
    assert state.ensure_mount_selected().mount_point == Path("F:\\")

    state.set_library_mode(True)
    state.apply_watch_event(
        "appeared", VolumeInfo(name="G: USB", mount_point=Path("G:\\"), is_removable=True)
    )
    assert state.library_mode is True
    assert state.current_mount == Path("F:\\")
    assert [s.display_name for s in state.visible_saves()] == ["PSP Game"]


def test_library_entry_status_resolves_without_a_prior_list(tmp_path):
    """A catalog row asks the library for its status, never a device hash."""
    from vajsave.library import catalog_entries, load_catalog

    lib = tmp_path / "lib"
    _library_backup(tmp_path, lib, "gba", "AGBE01", "GBA Game", datetime(2024, 1, 1, 10, 0, 0))
    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)

    entry = catalog_entries(load_catalog(lib), lib)[0]
    status = state.save_status(entry)
    assert status.status == "unchanged"
    assert status.sha256


def test_backup_refuses_a_source_inside_the_library(tmp_path):
    """Even outside library-mode, a library-owned path must never be re-copied."""
    lib = tmp_path / "lib"
    inside = lib / "gba" / "GAME" / "default" / "2024-01-01_10-00-00"
    inside.mkdir(parents=True)
    (inside / "save.bin").write_bytes(b"payload")

    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    entry = SaveEntry(
        platform="gba", source_id="gba", display_name="GAME", path=str(inside)
    )
    assert state.import_save(entry) is None
    assert state.status_text == "本地存档库无需备份"


def test_import_save_persists_resolved_rom_identity_key(tmp_path):
    """The backup path stores the ROM identity the cover cache is keyed by."""
    from vajsave.library import game_key, load_catalog

    lib = tmp_path / "lib"
    folder = tmp_path / "src" / "ULJM05800"
    folder.mkdir(parents=True)
    (folder / "DATA.BIN").write_bytes(b"save-data")
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="PSP Game",
        path=str(folder),
        title_id="ULJM05800",
    )

    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    assert state.import_save(entry) is not None

    game = load_catalog(lib).games[game_key(entry)]
    assert game.identity_key == "psp:ULJM05800"
    assert game.id == game_key(entry)


def test_library_row_uses_persisted_identity_key(tmp_path):
    from vajsave.library import load_catalog, save_catalog

    lib = tmp_path / "lib"
    _library_backup(tmp_path, lib, "gba", "AGBE01", "GBA Game", datetime(2024, 1, 1, 10, 0, 0))
    catalog = load_catalog(lib)
    game = next(iter(catalog.games.values()))
    game.identity_key = "gba:sha1:" + "a" * 40
    save_catalog(lib, catalog)

    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    state.set_library_mode(True)
    entry = state.visible_saves()[0]
    result = state.resolve_save_identity(entry)
    assert result.status == "resolved"
    assert result.identity_key == "gba:sha1:" + "a" * 40
    assert result.identity.title == "GBA Game"


def test_library_row_without_identity_key_falls_back_to_catalog_id(tmp_path):
    lib = tmp_path / "lib"
    _library_backup(tmp_path, lib, "gba", "AGBE01", "GBA Game", datetime(2024, 1, 1, 10, 0, 0))

    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    state.set_library_mode(True)
    entry = state.visible_saves()[0]
    result = state.resolve_save_identity(entry)
    assert result.status == "resolved"
    # A legacy record keeps its catalog id; no ROM identity is fabricated.
    assert result.identity_key == entry.extra["library_game_id"]


def test_delete_library_game_removes_catalog_game(tmp_path):
    from vajsave.library import load_catalog

    lib = tmp_path / "lib"
    _library_backup(tmp_path, lib, "psp", "ULJM05800", "PSP Game", datetime(2024, 1, 1, 10, 0, 0))
    _library_backup(tmp_path, lib, "gba", "AGBE01", "GBA Game", datetime(2024, 2, 1, 10, 0, 0))

    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    state.set_library_mode(True)
    entry = next(s for s in state.visible_saves() if s.platform == "psp")
    game_id = entry.extra["library_game_id"]

    result = state.delete_library_game(entry)

    assert result.ok is True
    assert game_id not in load_catalog(lib).games
    assert all(s.extra["library_game_id"] != game_id for s in state.visible_saves())
    assert "删除" in state.status_text


def test_delete_library_game_partial_failure_updates_status(tmp_path, monkeypatch):
    import vajsave.library as library_module

    lib = tmp_path / "lib"
    _library_backup(tmp_path, lib, "psp", "ULJM05800", "PSP Game", datetime(2024, 1, 1, 10, 0, 0))
    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    state.set_library_mode(True)
    entry = state.visible_saves()[0]

    monkeypatch.setattr(library_module, "_delete_snapshot_payload", lambda snap, root: False)

    result = state.delete_library_game(entry)

    assert result.ok is False
    assert "删除" in state.status_text


# -- FTP pull ----------------------------------------------------------------


def _psp_volume(tmp_path, psp_sfo_bytes):
    psp_root = tmp_path / "PSP_VOL"
    save_dir = psp_root / "PSP" / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    (save_dir / "DATA.BIN").write_bytes(b"DATA")
    return VolumeInfo(name="PSP_VOL", mount_point=psp_root)


def test_ftp_default_preset_is_checkpoint_with_ftpd_fallback(tmp_path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    keys = [preset.key for preset in state.ftp_presets()]
    assert state.ftp_preset_key == "checkpoint"
    assert keys[0] == "checkpoint"
    assert "ftpd" in keys
    assert state.current_ftp_preset().key == "checkpoint"


def test_set_ftp_preset_switches_and_persists(tmp_path):
    lib = tmp_path / "lib"
    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    assert state.set_ftp_preset("ftpd") == "ftpd"
    assert state.current_ftp_preset().key == "ftpd"
    assert state.set_ftp_preset("nope") is None
    assert state.ftp_preset_key == "ftpd"
    # A fresh state reads the persisted choice back.
    assert AppState(provider=FakeVolumeProvider([]), library_root=lib).ftp_preset_key == "ftpd"


def test_pull_ftp_selects_cache_as_device(tmp_path):
    factory = fake_client_factory(checkpoint_ftp_tree())
    lib = tmp_path / "lib"
    state = AppState(
        provider=FakeVolumeProvider([]), library_root=lib, ftp_client_factory=factory
    )

    result = state.pull_ftp_saves()

    assert result.ok is True
    assert state.current_mount == lib / "ftp-cache" / "checkpoint"
    assert state.library_mode is False
    assert state.current_result is not None
    assert state.current_result.saves
    platforms = {save.platform for save in state.current_result.saves}
    assert "3ds" in platforms
    assert "switch" in platforms
    assert "[只读]" in state.status_text


def test_pull_ftp_failure_does_not_replace_current_device(tmp_path, psp_sfo_bytes):
    vol = _psp_volume(tmp_path, psp_sfo_bytes)
    factory = fake_client_factory(
        checkpoint_ftp_tree(),
        fail_paths={"/3ds/Checkpoint/saves"},
        error_message="permission denied",
    )
    lib = tmp_path / "lib"
    state = AppState(
        provider=FakeVolumeProvider([vol]),
        library_root=lib,
        ftp_client_factory=factory,
    )
    state.refresh_volumes()
    state.select_mount(vol.mount_point)

    result = state.pull_ftp_saves()

    assert result.ok is False
    # The selected device is unchanged and the half-pull is never selected.
    assert state.current_mount == vol.mount_point
    assert not (lib / "ftp-cache" / "checkpoint").exists()
    assert any("FTP" in warning for warning in state.warnings)


def test_pull_ftp_failure_never_leaks_password(tmp_path):
    secret = "topsecret-xyz"
    factory = fake_client_factory(
        {}, fail_paths={"/"}, error_message=f"login failed for {secret}"
    )
    state = AppState(
        provider=FakeVolumeProvider([]),
        library_root=tmp_path / "lib",
        ftp_client_factory=factory,
    )
    state.configure_ftp(host="10.0.0.9", password=secret)

    result = state.pull_ftp_saves()

    assert result.ok is False
    assert secret not in result.error
    assert all(secret not in warning for warning in state.warnings)
    assert secret not in state.status_text


def test_pull_ftp_success_keeps_card_and_folder_behavior(tmp_path, psp_sfo_bytes):
    vol = _psp_volume(tmp_path, psp_sfo_bytes)
    lib = tmp_path / "lib"
    state = AppState(
        provider=FakeVolumeProvider([vol]),
        library_root=lib,
        ftp_client_factory=fake_client_factory(checkpoint_ftp_tree()),
    )
    state.refresh_volumes()

    # Card selection still works before an FTP pull.
    assert state.select_mount(vol.mount_point).platform == "psp"

    result = state.pull_ftp_saves()
    assert result.ok is True

    # ...and still works after (no device scan state leaked into the card path).
    assert state.select_mount(vol.mount_point).platform == "psp"
    assert state.current_mount == vol.mount_point

    # A manually added folder is still selectable too.
    folder = tmp_path / "legacy_backup"
    folder.mkdir()
    state.select_custom_path(folder)
    assert state.current_mount == folder


# --- optional LLM cover disambiguation ---------------------------------------


def test_llm_cover_disabled_by_default(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "config.json"))
    state = AppState(library_root=tmp_path / "lib")
    assert state.llm_cover_enabled is False
    assert state.llm_api_key == ""
    assert state.artwork_service.llm_chooser is None


def test_set_llm_cover_persists_key_but_never_leaks_it(tmp_path: Path, monkeypatch):
    from vajsave.library import load_app_config

    cfg = tmp_path / "config.json"
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(cfg))
    secret = "sk-llm-secret-xyz"
    state = AppState(library_root=tmp_path / "lib")

    state.set_llm_cover(enabled=True, api_key=secret)

    assert state.llm_cover_enabled is True
    assert state.llm_api_key == secret
    config = load_app_config()
    assert config["llm_cover_enabled"] is True
    assert config["llm_api_key"] == secret
    assert secret not in state.status_text
    assert all(secret not in warning for warning in state.warnings)
    assert secret not in repr(state.artwork_service.llm_chooser)

    # Turning it off and clearing the key removes the persisted secret.
    state.set_llm_cover(enabled=False, api_key="")
    assert state.llm_cover_enabled is False
    assert state.artwork_service.llm_chooser is None
    assert "llm_api_key" not in load_app_config()


def test_llm_cover_enabled_without_key_has_no_chooser(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "config.json"))
    state = AppState(library_root=tmp_path / "lib")
    state.set_llm_cover(enabled=True)
    assert state.llm_cover_enabled is True
    assert state.llm_api_key == ""
    assert state.artwork_service.llm_chooser is None


def test_app_state_reads_llm_cover_from_config(tmp_path: Path, monkeypatch):
    from vajsave.library import save_app_config

    cfg = tmp_path / "config.json"
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(cfg))
    save_app_config({"llm_cover_enabled": True, "llm_api_key": "sk-from-config"})
    state = AppState(library_root=tmp_path / "lib")
    assert state.llm_cover_enabled is True
    assert state.llm_api_key == "sk-from-config"
    assert state.artwork_service.llm_chooser is not None


def test_llm_cover_enabled_config_values_are_coerced(tmp_path: Path, monkeypatch):
    from vajsave.library import save_app_config

    for raw, expected in ((True, True), ("true", True), ("off", False), (0, False)):
        cfg = tmp_path / f"config-{raw}.json"
        monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(cfg))
        save_app_config({"llm_cover_enabled": raw})
        state = AppState(library_root=tmp_path / "lib")
        assert state.llm_cover_enabled is expected


def test_llm_base_url_and_model_default_when_unset(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "config.json"))
    state = AppState(library_root=tmp_path / "lib")
    assert state.llm_base_url == DEFAULT_LLM_BASE_URL
    assert state.llm_model == DEFAULT_LLM_MODEL


def test_set_llm_cover_persists_base_url_and_model_and_uses_them(
    tmp_path: Path, monkeypatch
):
    from vajsave.library import load_app_config

    cfg = tmp_path / "config.json"
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(cfg))
    state = AppState(library_root=tmp_path / "lib")

    state.set_llm_cover(
        enabled=True,
        api_key="sk-key",
        base_url="https://gateway.example/v1/",
        model="my-model",
    )

    assert state.llm_base_url == "https://gateway.example/v1/"
    assert state.llm_model == "my-model"
    config = load_app_config()
    assert config["llm_base_url"] == "https://gateway.example/v1/"
    assert config["llm_model"] == "my-model"

    chooser = state.artwork_service.llm_chooser
    assert chooser is not None
    assert chooser.base_url == "https://gateway.example/v1"  # trailing slash trimmed
    assert chooser.model == "my-model"


def test_app_state_reads_llm_base_url_and_model_from_config(tmp_path: Path, monkeypatch):
    from vajsave.library import save_app_config

    cfg = tmp_path / "config.json"
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(cfg))
    save_app_config(
        {
            "llm_cover_enabled": True,
            "llm_api_key": "sk-from-config",
            "llm_base_url": "http://localhost:11434/v1",
            "llm_model": "llama3",
        }
    )
    state = AppState(library_root=tmp_path / "lib")
    assert state.llm_base_url == "http://localhost:11434/v1"
    assert state.llm_model == "llama3"
    chooser = state.artwork_service.llm_chooser
    assert chooser is not None
    assert chooser.base_url == "http://localhost:11434/v1"
    assert chooser.model == "llama3"


def test_llm_cover_blank_base_url_and_model_fall_back_to_defaults(
    tmp_path: Path, monkeypatch
):
    from vajsave.library import save_app_config

    cfg = tmp_path / "config.json"
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(cfg))
    save_app_config(
        {
            "llm_cover_enabled": True,
            "llm_api_key": "sk-key",
            "llm_base_url": "   ",
            "llm_model": "",
        }
    )
    state = AppState(library_root=tmp_path / "lib")
    assert state.llm_base_url == DEFAULT_LLM_BASE_URL
    assert state.llm_model == DEFAULT_LLM_MODEL
    chooser = state.artwork_service.llm_chooser
    assert chooser is not None
    assert chooser.base_url == DEFAULT_LLM_BASE_URL
    assert chooser.model == DEFAULT_LLM_MODEL


def test_set_llm_cover_clearing_base_url_and_model_restores_defaults(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "config.json"))
    state = AppState(library_root=tmp_path / "lib")
    state.set_llm_cover(
        enabled=True, api_key="sk", base_url="https://x.example/v1", model="m"
    )
    state.set_llm_cover(base_url="", model="   ")
    assert state.llm_base_url == DEFAULT_LLM_BASE_URL
    assert state.llm_model == DEFAULT_LLM_MODEL


# --- selectable OpenAI / Anthropic protocol ----------------------------------


def test_llm_protocol_defaults_to_openai(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "config.json"))
    state = AppState(library_root=tmp_path / "lib")
    assert state.llm_protocol == DEFAULT_LLM_PROTOCOL == PROTOCOL_OPENAI


def test_set_llm_cover_persists_protocol_and_chooser_uses_it(
    tmp_path: Path, monkeypatch
):
    from vajsave.library import load_app_config

    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "config.json"))
    state = AppState(library_root=tmp_path / "lib")

    state.set_llm_cover(enabled=True, api_key="sk-ant", protocol=PROTOCOL_ANTHROPIC)

    assert state.llm_protocol == PROTOCOL_ANTHROPIC
    assert load_app_config()["llm_protocol"] == PROTOCOL_ANTHROPIC
    chooser = state.artwork_service.llm_chooser
    assert chooser is not None
    assert chooser.protocol == PROTOCOL_ANTHROPIC


def test_app_state_reads_llm_protocol_from_config(tmp_path: Path, monkeypatch):
    from vajsave.library import save_app_config

    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "config.json"))
    save_app_config(
        {
            "llm_cover_enabled": True,
            "llm_api_key": "sk-ant",
            "llm_protocol": PROTOCOL_ANTHROPIC,
        }
    )
    state = AppState(library_root=tmp_path / "lib")
    assert state.llm_protocol == PROTOCOL_ANTHROPIC
    assert state.llm_base_url == DEFAULT_ANTHROPIC_BASE_URL
    assert state.llm_model == DEFAULT_ANTHROPIC_MODEL
    chooser = state.artwork_service.llm_chooser
    assert chooser is not None
    assert chooser.protocol == PROTOCOL_ANTHROPIC
    assert chooser.base_url == DEFAULT_ANTHROPIC_BASE_URL


def test_unknown_llm_protocol_falls_back_to_openai(tmp_path: Path, monkeypatch):
    from vajsave.library import save_app_config

    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "config.json"))
    save_app_config({"llm_protocol": "gemini"})
    state = AppState(library_root=tmp_path / "lib")
    assert state.llm_protocol == PROTOCOL_OPENAI
    assert state.llm_base_url == DEFAULT_LLM_BASE_URL
    assert state.llm_model == DEFAULT_LLM_MODEL


def test_switching_protocol_swaps_the_default_base_url(tmp_path: Path, monkeypatch):
    from vajsave.library import load_app_config

    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "config.json"))
    state = AppState(library_root=tmp_path / "lib")
    assert state.llm_base_url == DEFAULT_LLM_BASE_URL

    state.set_llm_cover(protocol=PROTOCOL_ANTHROPIC)
    assert state.llm_protocol == PROTOCOL_ANTHROPIC
    assert state.llm_base_url == DEFAULT_ANTHROPIC_BASE_URL
    assert load_app_config()["llm_base_url"] == DEFAULT_ANTHROPIC_BASE_URL


def test_switching_protocol_keeps_a_custom_base_url(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "config.json"))
    state = AppState(library_root=tmp_path / "lib")
    state.set_llm_cover(base_url="https://gateway.example/v1")

    state.set_llm_cover(protocol=PROTOCOL_ANTHROPIC)

    assert state.llm_protocol == PROTOCOL_ANTHROPIC
    assert state.llm_base_url == "https://gateway.example/v1"


def test_switching_protocol_from_a_custom_base_via_the_ui_field_is_preserved(
    tmp_path: Path, monkeypatch
):
    # The settings dialog always sends the current field text, so a protocol
    # switch must inspect the *incoming* value: only the old default is
    # rewritten, a typed gateway endpoint is left as-is.
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "config.json"))
    state = AppState(library_root=tmp_path / "lib")
    state.set_llm_cover(base_url="https://gateway.example/v1")

    state.set_llm_cover(
        protocol=PROTOCOL_ANTHROPIC, base_url="https://gateway.example/v1"
    )
    assert state.llm_base_url == "https://gateway.example/v1"

    state.set_llm_cover(protocol=PROTOCOL_OPENAI, base_url=DEFAULT_LLM_BASE_URL)
    assert state.llm_protocol == PROTOCOL_OPENAI
    assert state.llm_base_url == DEFAULT_LLM_BASE_URL

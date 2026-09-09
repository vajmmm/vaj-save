import os
import queue
import time
from pathlib import Path
import pytest

from vajsave.app_state import AppState
from vajsave.models import VolumeInfo
from vajsave.volume import FakeVolumeProvider
from vajsave.backend import FakeStorageBackend
from conftest import build_sfo


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


def test_build_app_structure():
    try:
        import tkinter as tk
        from vajsave.app_ui import build_app
        root = tk.Tk()
        root.withdraw()
    except Exception:
        pytest.skip("Tkinter display not available")

    state = AppState(provider=FakeVolumeProvider([]))
    app = build_app(state=state, root=root)
    assert app.root == root
    assert app.state == state
    app.on_refresh_clicked()
    app.on_watch_toggle()
    app.on_show_in_finder_clicked()
    app.on_copy_path_clicked()
    app.on_close()




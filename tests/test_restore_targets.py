"""Restore-target suggestions and auto-backup-on-insert defaults."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from vajsave.app_state import AppState
from vajsave.device_registry import BoundSource
from vajsave.library import backup_save, load_app_config
from vajsave.models import SaveEntry, VolumeInfo
from vajsave.volume import FakeVolumeProvider


def _psp_entry(path: Path) -> SaveEntry:
    return SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="PSP Game",
        path=str(path),
        title_id="ULJM05800",
    )


def test_auto_backup_on_insert_defaults_false(tmp_path: Path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    assert state.auto_backup_on_insert is False
    assert load_app_config().get("auto_backup_on_insert") in (False, None)


def test_set_auto_backup_on_insert_persists(tmp_path: Path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    assert state.set_auto_backup_on_insert(True) is True
    assert state.auto_backup_on_insert is True
    assert load_app_config()["auto_backup_on_insert"] is True

    fresh = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    assert fresh.auto_backup_on_insert is True
    assert fresh.set_auto_backup_on_insert(False) is False
    assert load_app_config()["auto_backup_on_insert"] is False


def test_suggested_restore_dir_prefers_bound_source(tmp_path: Path):
    mount = tmp_path / "card"
    bound = mount / "custom" / "PSP" / "SAVEDATA"
    bound.mkdir(parents=True)
    (mount / "PSP" / "SAVEDATA").mkdir(parents=True)
    last = tmp_path / "last-restore"
    last.mkdir()

    volume = VolumeInfo("TF", mount, True, extra={"volume_id": "mac:test-card"})
    state = AppState(
        provider=FakeVolumeProvider([volume]),
        library_root=tmp_path / "lib",
    )
    state.refresh_volumes()
    state.current_mount = mount
    state.last_restore_dir = last
    state.device_registry.record(
        "mac:test-card",
        label="TF",
        sources=[BoundSource("psp", "psp", "custom/PSP/SAVEDATA")],
        merge=False,
    )

    assert state.suggested_restore_dir(_psp_entry(bound / "ULJM05800")) == bound


def test_suggested_restore_dir_uses_platform_default(tmp_path: Path):
    mount = tmp_path / "card"
    savedata = mount / "PSP" / "SAVEDATA"
    savedata.mkdir(parents=True)
    last = tmp_path / "last-restore"
    last.mkdir()

    volume = VolumeInfo("TF", mount, True, extra={"volume_id": "mac:unbound"})
    state = AppState(
        provider=FakeVolumeProvider([volume]),
        library_root=tmp_path / "lib",
    )
    state.refresh_volumes()
    state.current_mount = mount
    state.last_restore_dir = last

    assert state.suggested_restore_dir(_psp_entry(savedata / "ULJM05800")) == savedata


def test_suggested_restore_dir_falls_back_to_last_restore_dir(tmp_path: Path):
    last = tmp_path / "last-restore"
    last.mkdir()
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    state.last_restore_dir = last

    assert state.suggested_restore_dir(_psp_entry(tmp_path / "missing")) == last


def test_suggested_restore_dir_skips_missing_last_restore_dir(tmp_path: Path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    state.last_restore_dir = tmp_path / "gone"
    assert state.suggested_restore_dir(_psp_entry(tmp_path / "missing")) is None


def test_restore_version_persists_last_restore_dir(tmp_path: Path):
    lib = tmp_path / "lib"
    folder = tmp_path / "src" / "psp" / "ULJM05800"
    folder.mkdir(parents=True)
    (folder / "save.bin").write_bytes(b"payload")
    entry = _psp_entry(folder)
    result = backup_save(entry, lib, when=datetime(2024, 1, 1, 10, 0, 0))
    dest = tmp_path / "restored-out"
    dest.mkdir()

    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    restored = state.restore_version(result.snapshot, dest)

    assert restored is not None
    assert state.last_restore_dir == dest
    assert Path(load_app_config()["last_restore_dir"]) == dest

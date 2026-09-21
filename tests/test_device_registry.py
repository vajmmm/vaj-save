from pathlib import Path

from vajsave.app_state import AppState
from vajsave.device_registry import (
    BoundSource,
    DeviceRegistry,
    device_key_for,
    relative_source_root,
)
from vajsave.models import VolumeInfo
from vajsave.volume import FakeVolumeProvider


def test_relative_source_root_rejects_parent(tmp_path):
    assert relative_source_root(tmp_path, str(tmp_path.parent)) is None


def test_record_and_usable_sources(tmp_path):
    mount = tmp_path / "card"
    (mount / "PSP" / "SAVEDATA").mkdir(parents=True)
    path = tmp_path / "devices.json"
    registry = DeviceRegistry(path)
    registry.record(
        "win:ABCD1234",
        label="MS",
        sources=[BoundSource("psp", "psp", "PSP/SAVEDATA")],
        merge=False,
    )
    usable = registry.usable_sources("win:ABCD1234", mount)
    assert usable[0].relative_root == "PSP/SAVEDATA"
    again = DeviceRegistry(path)
    assert again.get("win:ABCD1234")[0].platform == "psp"


def test_corrupt_devices_json_is_empty(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text("{not json", encoding="utf-8")
    assert DeviceRegistry(path).get("win:ABCD1234") == []


def test_merge_keeps_existing_present_roots(tmp_path):
    mount = tmp_path / "card"
    (mount / "PSP" / "SAVEDATA").mkdir(parents=True)
    (mount / "switch" / "Checkpoint" / "saves").mkdir(parents=True)
    registry = DeviceRegistry(tmp_path / "devices.json")
    registry.record("win:1", label="x", sources=[BoundSource("psp", "psp", "PSP/SAVEDATA")], merge=False)
    registry.record(
        "win:1",
        label="x",
        sources=[BoundSource("switch", "switch_checkpoint", "switch/Checkpoint/saves")],
        merge=True,
    )
    roots = {item.relative_root for item in registry.get("win:1")}
    assert roots == {"PSP/SAVEDATA", "switch/Checkpoint/saves"}


def test_device_key_custom_and_ftp(tmp_path):
    assert device_key_for(tmp_path, {"custom": True}) == f"path:{tmp_path.resolve()}"
    assert device_key_for(tmp_path, {"ftp": True, "ftp_preset": "checkpoint"}) == "ftp:checkpoint"
    assert device_key_for(tmp_path, {"usb": "vid:pid"}) is None


def _psp_card(tmp_path: Path, psp_sfo_bytes: bytes) -> Path:
    mount = tmp_path / "card"
    save = mount / "PSP" / "SAVEDATA" / "ULJM05800"
    save.mkdir(parents=True)
    (save / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    (save / "DATA.BIN").write_bytes(b"psp")
    saver = mount / "SAVER"
    saver.mkdir()
    (saver / "decoy.sav").write_bytes(b"gba")
    return mount


def test_select_mount_records_first_bind(tmp_path, psp_sfo_bytes):
    mount = _psp_card(tmp_path, psp_sfo_bytes)
    vol = VolumeInfo(name="MS", mount_point=mount, extra={"volume_id": "win:ABCD1234"})
    state = AppState(
        provider=FakeVolumeProvider([vol]),
        library_root=tmp_path / "lib",
    )
    state.refresh_volumes()
    state.select_mount(mount)
    roots = {item.relative_root for item in state.device_registry.get("win:ABCD1234")}
    assert roots == {"PSP/SAVEDATA", "SAVER"}


def test_later_insert_scans_only_bound_roots(tmp_path, psp_sfo_bytes):
    mount = _psp_card(tmp_path, psp_sfo_bytes)
    vol = VolumeInfo(name="MS", mount_point=mount, extra={"volume_id": "win:ABCD1234"})
    state = AppState(
        provider=FakeVolumeProvider([vol]),
        library_root=tmp_path / "lib",
    )
    state.refresh_volumes()
    state.device_registry.record(
        "win:ABCD1234",
        label="MS",
        sources=[BoundSource("psp", "psp", "PSP/SAVEDATA")],
        merge=False,
    )
    result = state.select_mount(mount)
    assert {entry.platform for entry in result.saves} == {"psp"}
    assert not any(entry.platform == "gba" for entry in result.saves)


def test_missing_bound_dirs_full_scan_rewrites(tmp_path, psp_sfo_bytes):
    mount = tmp_path / "card"
    saver = mount / "SAVER"
    saver.mkdir(parents=True)
    (saver / "decoy.sav").write_bytes(b"gba")
    vol = VolumeInfo(name="MS", mount_point=mount, extra={"volume_id": "win:ABCD1234"})
    state = AppState(
        provider=FakeVolumeProvider([vol]),
        library_root=tmp_path / "lib",
    )
    state.refresh_volumes()
    state.device_registry.record(
        "win:ABCD1234",
        label="MS",
        sources=[BoundSource("psp", "psp", "PSP/SAVEDATA")],
        merge=False,
    )
    result = state.select_mount(mount)
    assert any(entry.platform == "gba" for entry in result.saves)
    roots = {item.relative_root for item in state.device_registry.get("win:ABCD1234")}
    assert roots == {"SAVER"}
    assert "PSP/SAVEDATA" not in roots


def test_refresh_merges_and_drops_missing(tmp_path, psp_sfo_bytes):
    mount = tmp_path / "card"
    save = mount / "PSP" / "SAVEDATA" / "ULJM05800"
    save.mkdir(parents=True)
    (save / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    slot = mount / "switch" / "Checkpoint" / "saves" / "Mario Kart 8"
    slot.mkdir(parents=True)
    (slot / "save.bin").write_bytes(b"switch")
    vol = VolumeInfo(name="MS", mount_point=mount, extra={"volume_id": "win:1"})
    state = AppState(
        provider=FakeVolumeProvider([vol]),
        library_root=tmp_path / "lib",
    )
    state.refresh_volumes()
    state.device_registry.record(
        "win:1",
        label="x",
        sources=[
            BoundSource("psp", "psp", "PSP/SAVEDATA"),
            BoundSource("nds", "nds_twilight", "roms/nds"),
        ],
        merge=False,
    )
    state.select_mount(mount, refresh=True)
    roots = {item.relative_root for item in state.device_registry.get("win:1")}
    assert "PSP/SAVEDATA" in roots
    assert "switch/Checkpoint/saves" in roots
    assert "roms/nds" not in roots


def test_no_volume_id_does_not_write_devices_json(tmp_path, psp_sfo_bytes):
    mount = _psp_card(tmp_path, psp_sfo_bytes)
    state = AppState(library_root=tmp_path / "lib")
    state.select_mount(mount)
    devices_path = state.settings.config_dir() / "devices.json"
    assert not devices_path.exists()


def test_zero_serial_does_not_become_registry_key(tmp_path, psp_sfo_bytes):
    mount = _psp_card(tmp_path, psp_sfo_bytes)
    vol = VolumeInfo(name="MS", mount_point=mount, extra={"volume_id": "win:00000000"})
    state = AppState(
        provider=FakeVolumeProvider([vol]),
        library_root=tmp_path / "lib",
    )
    state.refresh_volumes()
    state.select_mount(mount)
    assert state.device_registry.get("win:00000000") == []
    assert not (state.settings.config_dir() / "devices.json").exists()


def test_bound_scan_type_error_is_not_full_scan(tmp_path, psp_sfo_bytes):
    mount = _psp_card(tmp_path, psp_sfo_bytes)
    vol = VolumeInfo(name="MS", mount_point=mount, extra={"volume_id": "win:ABCD1234"})
    calls = []

    def exploding_scan(path, progress=None, bound_sources=None):
        calls.append(bound_sources)
        if bound_sources:
            raise TypeError("save parser exploded")
        raise AssertionError("must not fall back to an unbound full scan")

    state = AppState(
        provider=FakeVolumeProvider([vol]),
        scan_fn=exploding_scan,
        library_root=tmp_path / "lib",
    )
    state.refresh_volumes()
    state.device_registry.record(
        "win:ABCD1234",
        label="MS",
        sources=[BoundSource("psp", "psp", "PSP/SAVEDATA")],
        merge=False,
    )
    result = state.select_mount(mount)
    assert calls and calls[0]
    assert result.saves == []
    assert any("save parser exploded" in warning for warning in result.warnings)


def test_custom_folder_uses_path_key(tmp_path, psp_sfo_bytes):
    mount = tmp_path / "folder"
    save = mount / "PSP" / "SAVEDATA" / "ULJM05800"
    save.mkdir(parents=True)
    (save / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    state = AppState(library_root=tmp_path / "lib")
    state.select_custom_path(mount)
    key = f"path:{mount.resolve()}"
    assert state.device_registry.get(key)[0].relative_root == "PSP/SAVEDATA"

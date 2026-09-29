"""Tests for MTP discovery, VolumeInfo mapping, hotplug and AppState wiring."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from vajsave import mtp_windows
from vajsave.app_state import AppState
from vajsave.mtp_fetch import device_cache_dir
from vajsave.models import VolumeInfo
from vajsave.mtp_session import volume_name_for_device
from vajsave.remote_mtp import (
    MtpDevice,
    MtpError,
    MtpStorage,
    is_switch_mtp_device,
)
from vajsave.volume import FakeVolumeProvider

from conftest import FakeMtpClient, dbi_saves_tree

SAVES_STORAGES = [("saves", "7: Saves")]


def _dbi_device() -> MtpDevice:
    return MtpDevice("dev-1", "DBI", (MtpStorage("saves", "7: Saves"),))


def _phone_device() -> MtpDevice:
    return MtpDevice(
        "phone-1",
        "Android Phone",
        (MtpStorage("internal", "Internal shared storage"), MtpStorage("sdcard", "SD card")),
    )


def _factory(tree=None):
    payload = tree if tree is not None else dbi_saves_tree()
    return lambda device_id: FakeMtpClient(SAVES_STORAGES, payload)


def _changed_tree():
    tree = dbi_saves_tree()
    tree["saves"]["Installed games"]["0100000000010000 Super Mario Odyssey"]["Alice"][
        "main"
    ] = b"ALICE-CHANGED"
    return tree


# -- filtering -----------------------------------------------------------------


def test_is_switch_mtp_device_filtering():
    assert is_switch_mtp_device(_dbi_device())
    assert is_switch_mtp_device(MtpDevice("x", "Nintendo Switch", ()))
    assert is_switch_mtp_device(MtpDevice("x", "Unknown", (MtpStorage("s", "7: Saves"),)))
    assert not is_switch_mtp_device(_phone_device())


def test_volume_name_prefers_nintendo_names():
    assert volume_name_for_device(MtpDevice("x", "Nintendo Switch", ())) == "Nintendo Switch"
    assert volume_name_for_device(MtpDevice("x", "DBI", ())) == "Switch · DBI"
    assert volume_name_for_device(MtpDevice("x", "", ())) == "Switch · DBI MTP"


def test_devices_lister_error_is_empty(tmp_path: Path):
    def boom():
        raise OSError("WPD exploded")

    state = AppState(
        provider=FakeVolumeProvider([]),
        mtp_device_lister=boom,
        library_root=tmp_path / "lib",
    )
    assert state.mtp.devices() == []
    assert state.mtp.list_volumes() == []


def test_devices_skips_malformed_entries(tmp_path: Path):
    state = AppState(
        provider=FakeVolumeProvider([]),
        mtp_device_lister=lambda: [object(), _dbi_device()],
        library_root=tmp_path / "lib",
    )
    assert [device.device_id for device in state.mtp.devices()] == ["dev-1"]


# -- discovery / volume list ---------------------------------------------------


def test_refresh_volumes_merges_mtp_and_excludes_phone(tmp_path: Path):
    usb = FakeVolumeProvider(
        [VolumeInfo(name="USB", mount_point=tmp_path / "usb", is_removable=True)]
    )
    state = AppState(
        provider=usb,
        mtp_device_lister=lambda: [_dbi_device(), _phone_device()],
        library_root=tmp_path / "lib",
    )

    volumes = state.refresh_volumes()

    mtp_volumes = [v for v in volumes if (v.extra or {}).get("mtp")]
    assert len(mtp_volumes) == 1
    volume = mtp_volumes[0]
    assert volume.extra["mtp_id"] == "dev-1"
    assert volume.extra["platform"] == "switch"
    assert volume.is_removable is True
    assert Path(volume.mount_point) == device_cache_dir(tmp_path / "lib", "dev-1")
    assert "DBI" in volume.name
    assert not any(v.name == "Android Phone" for v in volumes)
    assert any(v.name == "USB" for v in volumes)


def test_non_windows_discovery_is_empty(monkeypatch):
    monkeypatch.setattr(mtp_windows.sys, "platform", "linux")
    assert mtp_windows.is_supported() is False
    assert mtp_windows.list_mtp_devices() == []


def test_windows_backend_is_used_when_platform_is_windows(monkeypatch):
    monkeypatch.setattr(mtp_windows.sys, "platform", "win32")

    class _Backend:
        def list_devices(self):
            return [_dbi_device()]

    assert mtp_windows.list_mtp_devices(backend=_Backend()) == [_dbi_device()]


def test_windows_backend_errors_are_swallowed(monkeypatch):
    monkeypatch.setattr(mtp_windows.sys, "platform", "win32")

    class _Backend:
        def list_devices(self):
            raise OSError("WPD 不可用")

    assert mtp_windows.list_mtp_devices(backend=_Backend()) == []


def test_open_client_unsupported_platform(monkeypatch):
    monkeypatch.setattr(mtp_windows.sys, "platform", "darwin")
    try:
        mtp_windows.open_mtp_client("dev-1")
    except MtpError:
        pass
    else:  # pragma: no cover - explicit failure
        raise AssertionError("expected MtpError on non-Windows")


def test_windows_default_backend_without_com_is_safe(monkeypatch):
    monkeypatch.setattr(mtp_windows.sys, "platform", "win32")
    # The real COM backend is unavailable here; discovery must fail closed.
    assert mtp_windows.list_mtp_devices() == []


def test_open_client_default_backend_on_windows(monkeypatch):
    monkeypatch.setattr(mtp_windows.sys, "platform", "win32")
    client = mtp_windows.open_mtp_client("dev-1")
    assert client is not None


# -- hotplug -------------------------------------------------------------------


def test_watch_sees_mtp_appear_and_disappear(tmp_path: Path):
    devices = {"current": []}
    state = AppState(
        provider=FakeVolumeProvider([]),
        mtp_device_lister=lambda: list(devices["current"]),
        mtp_client_factory=_factory(),
        library_root=tmp_path / "lib",
    )
    state.start_watch(interval=0.05)
    try:
        time.sleep(0.12)
        state.drain_events()
        assert not any((v.extra or {}).get("mtp") for v in state.volumes)

        devices["current"] = [_dbi_device()]
        time.sleep(0.25)
        state.drain_events()
        mount = device_cache_dir(tmp_path / "lib", "dev-1")
        assert any(
            Path(v.mount_point) == mount and (v.extra or {}).get("mtp")
            for v in state.volumes
        )

        devices["current"] = []
        time.sleep(0.25)
        state.drain_events()
        assert not any((v.extra or {}).get("mtp") for v in state.volumes)
    finally:
        state.stop_watch(timeout=0.5)


# -- pull then scan via select_mount -------------------------------------------


def test_select_mount_pulls_mtp_then_scans(tmp_path: Path):
    state = AppState(
        provider=FakeVolumeProvider([]),
        mtp_device_lister=lambda: [_dbi_device()],
        mtp_client_factory=_factory(),
        library_root=tmp_path / "lib",
    )
    state.refresh_volumes()
    volume = next(v for v in state.volumes if (v.extra or {}).get("mtp"))

    result = state.select_mount(volume.mount_point)

    assert len(result.saves) == 2
    assert all(entry.source_id == "switch_dbi" for entry in result.saves)
    assert all(str(volume.mount_point) in entry.path for entry in result.saves)
    assert state.device_registry.get("mtp:dev-1")


def test_pull_failure_keeps_previous_cache_and_warns(tmp_path: Path):
    state = AppState(
        provider=FakeVolumeProvider([]),
        mtp_device_lister=lambda: [_dbi_device()],
        mtp_client_factory=_factory(),
        library_root=tmp_path / "lib",
    )
    state.refresh_volumes()
    volume = next(v for v in state.volumes if (v.extra or {}).get("mtp"))
    state.select_mount(volume.mount_point)
    saved = (
        Path(volume.mount_point)
        / "Installed games"
        / "0100000000010000 Super Mario Odyssey"
        / "Alice"
        / "main"
    )
    assert saved.read_bytes() == b"ALICE-SAVE"

    state._mtp_client_factory = lambda device_id: FakeMtpClient(
        SAVES_STORAGES,
        _changed_tree(),
        fail_paths={"/Installed games/0100000000010000 Super Mario Odyssey/Alice/main"},
        error_message="unplugged",
    )
    result = state.select_mount(volume.mount_point)

    assert saved.read_bytes() == b"ALICE-SAVE"
    assert len(result.saves) == 2
    assert any("拉取失败" in warning for warning in result.warnings)


def test_missing_device_pull_reports_failure(tmp_path: Path):
    state = AppState(
        provider=FakeVolumeProvider([]),
        mtp_device_lister=lambda: [],
        library_root=tmp_path / "lib",
    )
    mount = device_cache_dir(tmp_path / "lib", "gone")
    result = state.mtp.pull_mount(mount)
    assert result.ok is False
    assert result.error


def test_device_id_fallback_matches_discovered_cache_dir(tmp_path: Path):
    state = AppState(
        provider=FakeVolumeProvider([]),
        mtp_device_lister=lambda: [_dbi_device()],
        mtp_client_factory=_factory(),
        library_root=tmp_path / "lib",
    )
    # No refresh_volumes(): the cache dir is matched against discovery instead.
    mount = device_cache_dir(tmp_path / "lib", "dev-1")
    result = state.mtp.pull_mount(mount)
    assert result.ok is True
    assert result.device_id == "dev-1"


def test_client_factory_error_is_returned_not_raised(tmp_path: Path):
    def boom(_device_id):
        raise MtpError("设备已拔出")

    state = AppState(
        provider=FakeVolumeProvider([]),
        mtp_device_lister=lambda: [_dbi_device()],
        mtp_client_factory=boom,
        library_root=tmp_path / "lib",
    )
    state.refresh_volumes()
    volume = next(v for v in state.volumes if (v.extra or {}).get("mtp"))
    result = state.mtp.pull_mount(volume.mount_point)
    assert result.ok is False
    assert "设备已拔出" in result.error

import sys
import threading
import time
from pathlib import Path
import pytest
from vajsave.volume import (
    VolumeInfo,
    VolumeProvider,
    FakeVolumeProvider,
    MountedVolumeProvider,
    watch_volumes,
)
from vajsave.scanner import scan
from conftest import build_sfo


def test_fake_volume_provider():
    vols = [
        VolumeInfo(name="PSP_DRIVE", mount_point=Path("/Volumes/PSP")),
        VolumeInfo(name="GENERIC_USB", mount_point=Path("/Volumes/USB1")),
    ]
    provider = FakeVolumeProvider(vols)
    listed = provider.list_volumes()
    assert len(listed) == 2
    assert listed[0].name == "PSP_DRIVE"
    assert listed[1].name == "GENERIC_USB"


def test_mounted_volume_provider_with_custom_search_dirs(tmp_path: Path):
    provider = MountedVolumeProvider(search_dirs=[tmp_path])
    vol1 = tmp_path / "TEST_VOL1"
    vol1.mkdir()
    vol2 = tmp_path / "TEST_VOL2"
    vol2.mkdir()

    vols = provider.list_volumes()
    names = {v.name for v in vols}
    assert "TEST_VOL1" in names
    assert "TEST_VOL2" in names


def test_mounted_volume_provider_macos_branch(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_volumes = tmp_path / "Volumes"
    fake_volumes.mkdir()

    (fake_volumes / "Macintosh HD").mkdir()
    (fake_volumes / "Recovery").mkdir()
    (fake_volumes / "PSP_DRIVE").mkdir()
    (fake_volumes / "SD_CARD").mkdir()

    provider = MountedVolumeProvider()
    monkeypatch.setattr(
        "vajsave.volume.Path",
        lambda p: fake_volumes if str(p) == "/Volumes" else Path(p),
    )

    vols = provider._list_macos_volumes()
    names = {v.name for v in vols}
    assert "PSP_DRIVE" in names
    assert "SD_CARD" in names
    assert "Macintosh HD" not in names
    assert "Recovery" not in names


def test_mounted_volume_provider_linux_branch(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("USER", "testuser")

    media_dir = tmp_path / "media" / "testuser"
    media_dir.mkdir(parents=True)
    (media_dir / "USB_STICK").mkdir()

    provider = MountedVolumeProvider()
    monkeypatch.setattr(
        "vajsave.volume.Path",
        lambda p: media_dir if "media" in str(p) else Path(p),
    )

    vols = provider._list_linux_volumes()
    assert any(v.name == "USB_STICK" for v in vols)


def test_mounted_volume_provider_windows_branch(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sys, "platform", "win32")

    provider = MountedVolumeProvider()
    # Mock Path.exists to return True for D:\ only
    real_path = Path

    class MockPath:
        def __init__(self, p):
            self.p = str(p)

        def exists(self):
            return self.p.startswith("D:")

        def __str__(self):
            return self.p

    monkeypatch.setattr("vajsave.volume.Path", MockPath)
    vols = provider._list_windows_volumes()
    assert len(vols) == 1
    assert "D:" in vols[0].name


def test_mounted_volume_provider_default_os_dispatch(monkeypatch):
    provider = MountedVolumeProvider()
    monkeypatch.setattr(sys, "platform", "unknown_os")
    assert provider.list_volumes() == []


def test_watch_volumes_appearance_and_disappearance(tmp_path: Path, psp_sfo_bytes: bytes):
    vol1_dir = tmp_path / "VOL_PSP"
    vol1_dir.mkdir()
    psp_dir = vol1_dir / "PSP" / "SAVEDATA" / "ULJM05800"
    psp_dir.mkdir(parents=True)
    (psp_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)

    vol1 = VolumeInfo(name="VOL_PSP", mount_point=vol1_dir)
    vol2_dir = tmp_path / "VOL_2"
    vol2 = VolumeInfo(name="VOL_2", mount_point=vol2_dir)

    provider = FakeVolumeProvider([vol1])
    events = []

    def on_event(event_type: str, volume: VolumeInfo):
        events.append((event_type, volume.name))

    stop_event = threading.Event()

    thread = threading.Thread(
        target=watch_volumes,
        args=(provider, 0.05, on_event, stop_event),
    )
    thread.daemon = True
    thread.start()

    time.sleep(0.1)
    # Initial scan should report appeared for vol1
    assert ("appeared", "VOL_PSP") in events

    # Simulate new volume added
    vol2_dir.mkdir(exist_ok=True)
    provider.set_volumes([vol1, vol2])
    time.sleep(0.15)
    assert ("appeared", "VOL_2") in events

    # Simulate volume removed
    provider.set_volumes([vol2])
    time.sleep(0.15)
    assert ("disappeared", "VOL_PSP") in events

    stop_event.set()
    thread.join(timeout=1.0)


def test_watch_unmounted_during_scan(tmp_path: Path):
    vol_dir = tmp_path / "VANISHING_VOL"
    vol_dir.mkdir()
    vol = VolumeInfo(name="VANISHING_VOL", mount_point=vol_dir)

    provider = FakeVolumeProvider([vol])
    events = []

    def on_event(event_type: str, volume: VolumeInfo):
        events.append((event_type, volume.name))
        # Remove directory right as it's processed
        if vol_dir.exists():
            vol_dir.rmdir()

    stop_event = threading.Event()
    thread = threading.Thread(
        target=watch_volumes,
        args=(provider, 0.05, on_event, stop_event),
    )
    thread.daemon = True
    thread.start()

    time.sleep(0.15)
    stop_event.set()
    thread.join(timeout=1.0)

    # Process must not crash and event registered
    assert ("appeared", "VANISHING_VOL") in events


def test_watch_volumes_error_handling():
    class BrokenProvider:
        def list_volumes(self):
            raise OSError("I/O error")

    stop_event = threading.Event()
    stop_event.set()
    # Must not raise exception
    watch_volumes(BrokenProvider(), 0.01, None, stop_event)

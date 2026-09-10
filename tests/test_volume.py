import ctypes as _real_ctypes
import sys
import threading
import time
import types as _types
from pathlib import Path
import pytest
from vajsave.volume import (
    VolumeInfo,
    VolumeProvider,
    FakeVolumeProvider,
    MountedVolumeProvider,
    watch_volumes,
    _detect_volume_platform,
    _enumerate_windows_drives,
)
from vajsave.scanner import scan
from conftest import build_sfo


# --- helpers for Win32 drive enumeration tests -----------------------------

def _fake_kernel32(drive_specs, failing_type=(), logical_drives_error=False):
    """Build a duck-typed kernel32 stub.

    drive_specs maps an uppercase drive letter -> (drive_type, label).
    """

    class _FakeKernel32:
        def GetLogicalDrives(self):
            if logical_drives_error:
                raise OSError("GetLogicalDrives failed")
            mask = 0
            for letter in drive_specs:
                mask |= 1 << (ord(letter.upper()) - ord("A"))
            return mask

        def GetDriveTypeW(self, root):
            letter = root[0].upper()
            if letter in failing_type:
                raise OSError("GetDriveTypeW failed")
            return drive_specs[letter][0]

        def GetVolumeInformationW(self, root, buffer, *args):
            letter = root[0].upper()
            buffer.value = drive_specs[letter][1]
            return True

    return _FakeKernel32()


def _fake_ctypes(kernel32):
    return _types.SimpleNamespace(
        windll=_types.SimpleNamespace(kernel32=kernel32),
        create_unicode_buffer=_real_ctypes.create_unicode_buffer,
    )


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


def test_enumerate_windows_drives_bitmask_and_volumeinfo():
    kernel32 = _fake_kernel32(
        {
            "C": (3, ""),           # fixed
            "E": (2, ""),           # removable, no label
            "F": (2, "KINGSTON"),   # removable, labeled
            "G": (5, "DVD"),        # cdrom -> skipped
            "H": (4, "share"),      # remote -> skipped
            "I": (0, ""),           # unknown -> skipped
            "J": (1, ""),           # no root dir -> skipped
        }
    )
    vols = _enumerate_windows_drives(kernel32)
    by_letter = {v.mount_point.name[0]: v for v in vols}

    assert set(by_letter) == {"C", "E", "F"}
    assert by_letter["C"].is_removable is False
    assert by_letter["E"].is_removable is True
    assert by_letter["F"].is_removable is True

    assert by_letter["C"].name == "C: 本地磁盘"
    assert by_letter["E"].name == "E: 可移动磁盘"
    assert by_letter["F"].name == "F: KINGSTON"

    assert by_letter["F"].extra["drive_type"] == 2
    assert by_letter["F"].extra["label"] == "KINGSTON"
    assert by_letter["C"].extra["drive_type"] == 3
    assert by_letter["C"].extra["label"] == ""


def test_enumerate_windows_drives_removable_before_fixed():
    kernel32 = _fake_kernel32(
        {"C": (3, ""), "D": (3, ""), "E": (2, ""), "F": (2, "")}
    )
    vols = _enumerate_windows_drives(kernel32)
    assert [v.mount_point.name[0] for v in vols] == ["E", "F", "C", "D"]


def test_enumerate_windows_drives_skips_type_error():
    kernel32 = _fake_kernel32(
        {"C": (3, ""), "E": (2, "USB")}, failing_type=("E",)
    )
    vols = _enumerate_windows_drives(kernel32)
    assert [v.mount_point.name[0] for v in vols] == ["C"]


def test_enumerate_windows_drives_logical_error_returns_empty():
    kernel32 = _fake_kernel32({"C": (3, "")}, logical_drives_error=True)
    assert _enumerate_windows_drives(kernel32) == []


def test_enumerate_windows_drives_label_error_is_safe():
    class _Kernel32:
        def GetLogicalDrives(self):
            return 1 << (ord("C") - ord("A"))

        def GetDriveTypeW(self, root):
            return 3

        def GetVolumeInformationW(self, *args, **kwargs):
            raise OSError("label unavailable")

    vols = _enumerate_windows_drives(_Kernel32())
    assert len(vols) == 1
    assert vols[0].name == "C: 本地磁盘"
    assert vols[0].extra["label"] == ""


def test_mounted_volume_provider_windows_branch(monkeypatch):
    """Windows branch now enumerates via kernel32 (not Path.exists probing)."""
    kernel32 = _fake_kernel32({"D": (2, "USB_STICK")})
    monkeypatch.setattr("vajsave.volume.ctypes", _fake_ctypes(kernel32))

    vols = MountedVolumeProvider()._list_windows_volumes()
    assert len(vols) == 1
    assert "D:" in vols[0].name
    assert "USB_STICK" in vols[0].name
    assert vols[0].is_removable is True


def test_list_windows_volumes_without_ctypes_is_safe(monkeypatch):
    monkeypatch.setattr("vajsave.volume.ctypes", None)
    assert MountedVolumeProvider()._list_windows_volumes() == []


def test_list_windows_volumes_without_windll_is_safe(monkeypatch):
    monkeypatch.setattr("vajsave.volume.ctypes", _types.SimpleNamespace())
    assert MountedVolumeProvider()._list_windows_volumes() == []


def test_list_windows_volumes_annotates_platform(monkeypatch):
    kernel32 = _fake_kernel32({"E": (2, "USB")})
    monkeypatch.setattr("vajsave.volume.ctypes", _fake_ctypes(kernel32))
    monkeypatch.setattr("vajsave.volume.guess_platform", lambda root: "psp")

    vols = MountedVolumeProvider()._list_windows_volumes()
    assert len(vols) == 1
    assert vols[0].extra["platform"] == "psp"


def test_list_windows_volumes_platform_failure_does_not_break_enumeration(monkeypatch):
    kernel32 = _fake_kernel32({"E": (2, "USB")})
    monkeypatch.setattr("vajsave.volume.ctypes", _fake_ctypes(kernel32))

    def _boom(root):
        raise RuntimeError("platform probe failed")

    monkeypatch.setattr("vajsave.volume.guess_platform", _boom)

    vols = MountedVolumeProvider()._list_windows_volumes()
    assert len(vols) == 1
    assert "platform" not in (vols[0].extra or {})


def test_detect_volume_platform_uses_guess(tmp_path: Path):
    (tmp_path / "PSP" / "SAVEDATA").mkdir(parents=True)
    assert _detect_volume_platform(tmp_path) == "psp"


def test_detect_volume_platform_failure_returns_none(monkeypatch, tmp_path: Path):
    def _boom(root):
        raise RuntimeError("probe failed")

    monkeypatch.setattr("vajsave.volume.guess_platform", _boom)
    assert _detect_volume_platform(tmp_path) is None


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

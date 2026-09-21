import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Set, Tuple, Union

from .models import VolumeInfo
from .scanner import guess_platform
from .volume_id import format_windows_serial

try:  # pragma: no cover - platform dependent
    import ctypes
    from ctypes import create_unicode_buffer as _create_unicode_buffer
except Exception:  # pragma: no cover - ctypes is stdlib, but stay defensive
    ctypes = None  # type: ignore[assignment]
    _create_unicode_buffer = None  # type: ignore[assignment]

# Captured at import so tests can patch ``vajsave.volume.ctypes`` without
# losing ``c_uint32`` / ``byref`` needed to read the volume serial DWORD.
_C_UINT32 = getattr(ctypes, "c_uint32", None) if ctypes is not None else None
_BYREF = getattr(ctypes, "byref", None) if ctypes is not None else None

# GetDriveTypeW return codes.
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

# Only removable and fixed drives are interesting as save containers.
_KEPT_DRIVE_TYPES = (DRIVE_REMOVABLE, DRIVE_FIXED)
_DRIVE_TYPE_TEXT = {
    DRIVE_REMOVABLE: "可移动磁盘",
    DRIVE_FIXED: "本地磁盘",
}


def _read_volume_identity(kernel32: Any, root_path: str) -> Tuple[str, Optional[str]]:
    """Read label + serial DWORD through ``GetVolumeInformationW``, never raising."""
    try:
        buffer = _create_unicode_buffer(261)
        holder = _C_UINT32(0) if _C_UINT32 is not None else None
        serial_arg = _BYREF(holder) if holder is not None and _BYREF is not None else None
        kernel32.GetVolumeInformationW(
            root_path, buffer, len(buffer), serial_arg, None, None, None, 0
        )
        label = buffer.value or ""
        volume_id = format_windows_serial(int(holder.value)) if holder is not None else None
        return label, volume_id
    except Exception:
        return "", None


def _enumerate_windows_drives(kernel32: Any) -> List[VolumeInfo]:
    """Enumerate Windows drives from an injectable kernel32 (duck typed).

    Uses the ``GetLogicalDrives`` bitmask so every mounted drive letter is
    considered (not a hardcoded D..Z range). Removable drives sort before
    fixed drives, each group alphabetically by letter. All failures are
    swallowed so this never raises to the caller.
    """
    entries: List[Tuple[int, str, VolumeInfo]] = []

    try:
        mask = int(kernel32.GetLogicalDrives())
    except Exception:
        return []

    for index in range(26):
        if not (mask >> index) & 1:
            continue
        letter = chr(ord("A") + index)
        root_path = f"{letter}:\\"
        try:
            drive_type = int(kernel32.GetDriveTypeW(root_path))
        except Exception:
            continue
        if drive_type not in _KEPT_DRIVE_TYPES:
            continue

        label, volume_id = _read_volume_identity(kernel32, root_path)
        stripped = label.strip()
        if stripped:
            name = f"{letter}: {stripped}"
        else:
            name = f"{letter}: {_DRIVE_TYPE_TEXT.get(drive_type, '磁盘')}"

        try:
            mount_point = Path(root_path)
        except Exception:
            continue

        extra: Dict[str, Any] = {"drive_type": drive_type, "label": label}
        if volume_id:
            extra["volume_id"] = volume_id

        entries.append(
            (drive_type, letter, VolumeInfo(
                name=name,
                mount_point=mount_point,
                is_removable=(drive_type == DRIVE_REMOVABLE),
                extra=extra,
            ))
        )

    # Removable drives first; within each group ordered by drive letter.
    entries.sort(key=lambda item: (0 if item[0] == DRIVE_REMOVABLE else 1, item[1]))
    return [volume for _, _, volume in entries]


def _detect_volume_platform(mount_point: Path) -> Optional[str]:
    """Best-effort, shallow platform annotation for a mounted volume."""
    try:
        return guess_platform(mount_point)
    except Exception:
        return None


class VolumeProvider(Protocol):
    """Protocol for discovering available storage volumes."""

    def list_volumes(self) -> List[VolumeInfo]:
        ...


class FakeVolumeProvider:
    """Fake volume provider for testing."""

    def __init__(self, volumes: Optional[List[VolumeInfo]] = None) -> None:
        self._volumes: List[VolumeInfo] = list(volumes or [])

    def list_volumes(self) -> List[VolumeInfo]:
        return list(self._volumes)

    def set_volumes(self, volumes: List[VolumeInfo]) -> None:
        self._volumes = list(volumes)


class MountedVolumeProvider:
    """Default provider that discovers mounted volumes on the current OS."""

    SYSTEM_VOLUME_NAMES = {
        "Macintosh HD",
        "Macintosh HD - Data",
        "Recovery",
        "Preboot",
        "VM",
        "Update",
        "Hardware",
        "xarts",
        "iSCPreboot",
    }

    def __init__(self, search_dirs: Optional[List[Union[Path, str]]] = None) -> None:
        self.search_dirs = [Path(d) for d in search_dirs] if search_dirs is not None else None

    def list_volumes(self) -> List[VolumeInfo]:
        if self.search_dirs is not None:
            results: List[VolumeInfo] = []
            for sdir in self.search_dirs:
                if sdir.is_dir():
                    try:
                        for entry in sorted(sdir.iterdir()):
                            if entry.is_dir():
                                results.append(
                                    VolumeInfo(
                                        name=entry.name,
                                        mount_point=entry,
                                        is_removable=True,
                                    )
                                )
                    except OSError:
                        pass
            return results

        if sys.platform == "darwin":
            return self._list_macos_volumes()
        elif sys.platform.startswith("linux"):
            return self._list_linux_volumes()
        elif sys.platform == "win32":
            return self._list_windows_volumes()
        return []

    def _list_macos_volumes(self) -> List[VolumeInfo]:
        volumes_dir = Path("/Volumes")
        if not volumes_dir.is_dir():
            return []

        results: List[VolumeInfo] = []
        try:
            for entry in sorted(volumes_dir.iterdir()):
                try:
                    if not entry.is_dir():
                        continue
                    if entry.name in self.SYSTEM_VOLUME_NAMES:
                        continue
                    # /Volumes/Macintosh HD is often a symlink to /
                    if entry.is_symlink() and entry.resolve() == Path("/"):
                        continue
                    results.append(
                        VolumeInfo(
                            name=entry.name,
                            mount_point=entry,
                            is_removable=True,
                        )
                    )
                except OSError:
                    continue
        except OSError:
            pass

        return results

    def _list_linux_volumes(self) -> List[VolumeInfo]:
        candidates: List[Path] = [Path("/media"), Path("/run/media")]
        user = os.environ.get("USER")
        if user:
            candidates.extend([Path(f"/media/{user}"), Path(f"/run/media/{user}")])

        results: List[VolumeInfo] = []
        seen_paths: Set[Path] = set()

        for cdir in candidates:
            if not cdir.is_dir():
                continue
            try:
                for entry in sorted(cdir.iterdir()):
                    if entry.is_dir() and entry not in seen_paths:
                        seen_paths.add(entry)
                        results.append(
                            VolumeInfo(
                                name=entry.name,
                                mount_point=entry,
                                is_removable=True,
                            )
                        )
            except OSError:
                continue

        return results

    def _list_windows_volumes(self) -> List[VolumeInfo]:
        try:
            kernel32 = ctypes.windll.kernel32
        except Exception:
            return []

        try:
            volumes = _enumerate_windows_drives(kernel32)
        except Exception:
            return []

        for volume in volumes:
            platform = _detect_volume_platform(volume.mount_point)
            if platform:
                if volume.extra is None:
                    volume.extra = {}
                volume.extra.setdefault("platform", platform)
        return volumes


def watch_volumes(
    provider: VolumeProvider,
    interval: float = 1.0,
    callback: Optional[Callable[[str, VolumeInfo], None]] = None,
    stop_event: Optional[Any] = None,
) -> None:
    """Poll for volume changes and notify callback on appearance/disappearance."""
    known: Dict[Path, VolumeInfo] = {}

    while stop_event is None or not stop_event.is_set():
        try:
            current_list = provider.list_volumes()
            current_map = {v.mount_point: v for v in current_list}

            # Appeared volumes
            for mount, vol in current_map.items():
                if mount not in known:
                    known[mount] = vol
                    if callback:
                        try:
                            callback("appeared", vol)
                        except Exception:
                            pass

            # Disappeared volumes
            disappeared_mounts = [m for m in known if m not in current_map]
            for mount in disappeared_mounts:
                old_vol = known.pop(mount)
                if callback:
                    try:
                        callback("disappeared", old_vol)
                    except Exception:
                        pass
        except Exception:
            pass

        if stop_event and stop_event.wait(interval):
            break
        elif stop_event is None:
            time.sleep(interval)

import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Set, Union

from .models import VolumeInfo


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
        results: List[VolumeInfo] = []
        for drive_letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            drive_path = Path(f"{drive_letter}:\\")
            if drive_path.exists():
                results.append(
                    VolumeInfo(
                        name=f"Drive ({drive_letter}:)",
                        mount_point=drive_path,
                        is_removable=True,
                    )
                )
        return results


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

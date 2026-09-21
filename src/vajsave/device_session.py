"""Volume list, custom folders, preferred device, and hotplug watch."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple, Union

from .models import VolumeInfo
from .volume import watch_volumes

if TYPE_CHECKING:
    from .app_state import AppState


def _mount_sort_key(volume: VolumeInfo) -> Tuple[int, int]:
    """Ranking key for auto-selecting a removable device.

    Removable volumes (USB sticks, handhelds exposing themselves as UMS drives)
    are the only auto-select candidates, and inside that group the highest
    Windows drive letter wins, because a freshly attached device is normally
    handed the next free letter. Volumes without a drive letter keep their
    provider order.
    """
    text = str(volume.mount_point)
    letter = -1
    if len(text) >= 2 and text[1] == ":":
        char = text[0].upper()
        if "A" <= char <= "Z":
            letter = ord(char)
    return (0 if volume.is_removable else 1, -letter)


class DeviceSession:
    def __init__(self, app: AppState) -> None:
        self.app = app

    def refresh_volumes(self) -> List[VolumeInfo]:
        """Fetch latest volume list from provider.

        Pulled FTP caches are not provider volumes, so they are preserved across
        a refresh (when they still exist) instead of vanishing from the device
        list.
        """
        app = self.app
        preserved = [
            volume
            for volume in app.volumes
            if (volume.extra or {}).get("ftp") and Path(volume.mount_point).is_dir()
        ]
        try:
            app.volumes = app.provider.list_volumes()
        except Exception as e:
            app.warnings.append(f"刷新卷列表失败: {e}")
            app.volumes = []
        for volume in preserved:
            if not any(
                Path(existing.mount_point) == Path(volume.mount_point)
                for existing in app.volumes
            ):
                app.volumes.append(volume)
        return app.volumes

    def register_custom_path(self, path: Union[Path, str]) -> Path:
        """把用户选择的目录加入设备列表，但不立即扫描。"""
        app = self.app
        custom_path = Path(path)
        exists_in_volumes = any(v.mount_point == custom_path for v in app.volumes)
        if not exists_in_volumes:
            name = custom_path.name or str(custom_path)
            app.volumes.append(
                VolumeInfo(
                    name=f"文件夹: {name}",
                    mount_point=custom_path,
                    is_removable=False,
                )
            )
        return custom_path

    def select_custom_path(self, path: Union[Path, str]):
        """Select an arbitrary folder from file dialog and scan it."""
        custom_path = self.register_custom_path(path)
        return self.app.select_mount(custom_path)

    def preferred_volume(self) -> Optional[VolumeInfo]:
        """The removable device the app should default to, else ``None``.

        Fixed disks (C:/D:/E:) are deliberately never auto-selected: a built-in
        drive is not a handheld card, and scanning it on startup is both slow and
        surprising. The user can still open one explicitly through the
        "其他设备" entry.
        """
        removable = [volume for volume in self.app.volumes if volume.is_removable]
        if not removable:
            return None
        return min(removable, key=_mount_sort_key)

    def mount_selection_candidate(self) -> Optional[VolumeInfo]:
        """返回当前应该自动扫描的设备，但不执行扫描。"""
        app = self.app
        if app.library_mode:
            return None
        preferred = self.preferred_volume()
        if preferred is None:
            return None
        if app.current_mount is not None:
            if not app._auto_selected_mount:
                return None
            if Path(preferred.mount_point) == Path(app.current_mount):
                return None
        return preferred

    def ensure_mount_selected(self) -> Optional[VolumeInfo]:
        """Pick a default device when none is chosen, or upgrade an automatic choice.

        Called after enumerating volumes so a just-attached USB stick or handheld
        on a high drive letter (F:) is preferred over built-in C:/D:/E: drives.
        A device the user picked themselves is never replaced, and while the user
        is browsing the local library the source is left alone entirely.
        """
        preferred = self.mount_selection_candidate()
        if preferred is None:
            return None
        self.app.select_mount(preferred.mount_point, auto=True)
        return preferred

    def apply_watch_event(
        self, event_type: str, volume: VolumeInfo, *, auto_select: bool = True
    ) -> None:
        """Handle volume appearance or disappearance."""
        app = self.app
        v_mount = Path(volume.mount_point)
        if event_type == "appeared":
            idx = next(
                (i for i, v in enumerate(app.volumes) if v.mount_point == v_mount),
                None,
            )
            if idx is None:
                app.volumes.append(volume)
            else:
                app.volumes[idx] = volume

            # Re-evaluate instead of accepting whichever volume arrived first, so
            # the startup burst of events settles on the preferred device.
            before = app.current_mount
            if auto_select:
                self.ensure_mount_selected()
            if (
                (not auto_select and app.current_mount != v_mount)
                or (auto_select and app.current_mount == before)
            ):
                app.status_text = f"发现新设备: {volume.name}"

        elif event_type == "disappeared":
            app.volumes = [v for v in app.volumes if v.mount_point != v_mount]
            if app.current_mount == v_mount:
                app.current_mount = None
                app.current_result = None
                app._auto_selected_mount = False
                app._backup_statuses = {}
                app.warnings = []
                app.status_text = f"卷 {volume.name} 已卸载"
                if auto_select:
                    self.ensure_mount_selected()
            else:
                app.status_text = f"设备/卷已拔出: {volume.name}"

    def drain_events(self, *, auto_select: bool = True) -> int:
        """Process all queued watch events on the main thread."""
        count = 0
        while True:
            try:
                event_type, volume = self.app.event_queue.get_nowait()
            except queue.Empty:
                break
            self.apply_watch_event(event_type, volume, auto_select=auto_select)
            count += 1
        return count

    def start_watch(self, interval: float = 1.0) -> None:
        """Start background watcher thread."""
        app = self.app
        if app.is_watching:
            return
        app._stop_event = threading.Event()

        def _callback(event_type: str, vol: VolumeInfo) -> None:
            app.event_queue.put((event_type, vol))

        app._watch_thread = threading.Thread(
            target=watch_volumes,
            args=(app.provider, interval, _callback, app._stop_event),
            daemon=True,
        )
        app._watch_thread.start()
        app.is_watching = True

    def stop_watch(self, timeout: float = 1.0) -> None:
        """Stop background watcher thread."""
        app = self.app
        if not app.is_watching:
            return
        if app._stop_event is not None:
            app._stop_event.set()
        if app._watch_thread is not None and app._watch_thread.is_alive():
            app._watch_thread.join(timeout=timeout)
        app.is_watching = False
        app._watch_thread = None
        app._stop_event = None

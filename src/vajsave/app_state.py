import queue
import threading
from pathlib import Path
from typing import Callable, List, Optional, Union

from .backend import StorageBackend
from .models import ScanResult, VolumeInfo
from .scanner import scan
from .volume import MountedVolumeProvider, VolumeProvider, watch_volumes


def _build_status_text(result: ScanResult) -> str:
    """Build a human-readable status text for a ScanResult."""
    if not Path(result.root_path).exists():
        return f"路径不存在或不可读: {result.root_path}"

    # Check if this is an encrypted 3DS card
    has_encrypted_3ds = any(
        s.source_id == "3ds_sd" or (s.extra and s.extra.get("encrypted_container"))
        for s in result.sources
    )
    if has_encrypted_3ds and not result.saves:
        return "平台: 3DS | 发现加密 SD 卡 (Nintendo 3DS)，原生存档已加密，不可直接管理 | [只读]"

    if result.platform == "unknown" and not result.saves:
        return "平台: 未知/通用卷 | 发现 0 个可识别存档 | [只读]"

    source_count = len(result.sources)
    save_count = len(result.saves)
    return f"平台: {result.platform.upper()} | 来源: {source_count} 个 | 存档: {save_count} 个 | [只读]"


class AppState:
    """Pure Python state machine for the vaj-save Desktop App."""

    def __init__(
        self,
        provider: Optional[VolumeProvider] = None,
        scan_fn: Optional[Callable[[Union[Path, str]], ScanResult]] = None,
        backend: Optional[StorageBackend] = None,
    ) -> None:
        self.provider: VolumeProvider = provider or MountedVolumeProvider()
        self.scan_fn: Callable[[Union[Path, str]], ScanResult] = scan_fn or scan
        self.backend: Optional[StorageBackend] = backend

        self.volumes: List[VolumeInfo] = []
        self.current_mount: Optional[Path] = None
        self.current_result: Optional[ScanResult] = None
        self.status_text: str = "就绪"
        self.warnings: List[str] = []

        self.event_queue: "queue.Queue[tuple[str, VolumeInfo]]" = queue.Queue()
        self.is_watching: bool = False
        self._watch_thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None

    def refresh_volumes(self) -> List[VolumeInfo]:
        """Fetch latest volume list from provider."""
        try:
            self.volumes = self.provider.list_volumes()
        except Exception as e:
            self.warnings.append(f"刷新卷列表失败: {e}")
            self.volumes = []
        return self.volumes

    def select_mount(self, mount_point: Union[Path, str]) -> ScanResult:
        """Select a volume or path to scan and update current result."""
        path = Path(mount_point)
        self.current_mount = path
        try:
            res = self.scan_fn(path)
        except Exception as e:
            res = ScanResult(
                root_path=str(path),
                platform="unknown",
                sources=[],
                saves=[],
                warnings=[f"扫描异常: {e}"],
            )
        self.current_result = res
        self.warnings = list(res.warnings)
        self.status_text = _build_status_text(res)
        return res

    def select_custom_path(self, path: Union[Path, str]) -> ScanResult:
        """Select an arbitrary folder from file dialog and scan it."""
        custom_path = Path(path)
        exists_in_volumes = any(v.mount_point == custom_path for v in self.volumes)
        if not exists_in_volumes:
            name = custom_path.name or str(custom_path)
            self.volumes.append(
                VolumeInfo(
                    name=f"文件夹: {name}",
                    mount_point=custom_path,
                    is_removable=False,
                )
            )
        return self.select_mount(custom_path)

    def apply_watch_event(self, event_type: str, volume: VolumeInfo) -> None:
        """Handle volume appearance or disappearance."""
        v_mount = Path(volume.mount_point)
        if event_type == "appeared":
            idx = next((i for i, v in enumerate(self.volumes) if v.mount_point == v_mount), None)
            if idx is None:
                self.volumes.append(volume)
            else:
                self.volumes[idx] = volume

            if self.current_mount is None:
                self.select_mount(v_mount)
            else:
                self.status_text = f"发现新设备: {volume.name}"

        elif event_type == "disappeared":
            self.volumes = [v for v in self.volumes if v.mount_point != v_mount]
            if self.current_mount == v_mount:
                self.current_mount = None
                self.current_result = None
                self.warnings = []
                self.status_text = f"卷 {volume.name} 已卸载"
            else:
                self.status_text = f"设备/卷已拔出: {volume.name}"

    def drain_events(self) -> int:
        """Process all queued watch events on the main thread."""
        count = 0
        while True:
            try:
                event_type, volume = self.event_queue.get_nowait()
            except queue.Empty:
                break
            self.apply_watch_event(event_type, volume)
            count += 1
        return count

    def start_watch(self, interval: float = 1.0) -> None:
        """Start background watcher thread."""
        if self.is_watching:
            return
        self._stop_event = threading.Event()

        def _callback(event_type: str, vol: VolumeInfo) -> None:
            self.event_queue.put((event_type, vol))

        self._watch_thread = threading.Thread(
            target=watch_volumes,
            args=(self.provider, interval, _callback, self._stop_event),
            daemon=True,
        )
        self._watch_thread.start()
        self.is_watching = True

    def stop_watch(self, timeout: float = 1.0) -> None:
        """Stop background watcher thread."""
        if not self.is_watching:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        if self._watch_thread is not None and self._watch_thread.is_alive():
            self._watch_thread.join(timeout=timeout)
        self.is_watching = False
        self._watch_thread = None
        self._stop_event = None

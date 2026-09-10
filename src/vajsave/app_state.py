import queue
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

from .backend import StorageBackend
from .library import (
    BackupResult,
    Snapshot,
    collection_stats,
    default_library_root,
    backup_save,
    export_snapshot_zip,
    game_key,
    load_catalog,
    restore_snapshot,
    set_game_meta,
    versions_for,
)
from .models import SaveEntry, ScanResult, VolumeInfo
from .scanner import scan
from .volume import MountedVolumeProvider, VolumeProvider, watch_volumes

PLATFORM_ORDER = ["all", "psp", "vita", "switch", "3ds", "nds", "gba"]

PLATFORM_LABELS = {
    "all": "全部",
    "psp": "PSP",
    "vita": "PS Vita",
    "switch": "Switch",
    "3ds": "3DS",
    "nds": "NDS",
    "gba": "GBA",
}


def _build_status_text(result: ScanResult) -> str:
    """Build a human-readable status text for a ScanResult."""
    if not Path(result.root_path).exists():
        return f"路径不存在或不可读: {result.root_path}"

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
        library_root: Optional[Union[Path, str]] = None,
    ) -> None:
        self.provider: VolumeProvider = provider or MountedVolumeProvider()
        self.scan_fn: Callable[[Union[Path, str]], ScanResult] = scan_fn or scan
        self.backend: Optional[StorageBackend] = backend
        self.library_root: Path = Path(library_root) if library_root else default_library_root()
        self.last_import_path: Optional[Path] = None
        self.last_backup: Optional[BackupResult] = None

        self.volumes: List[VolumeInfo] = []
        self.current_mount: Optional[Path] = None
        self.current_result: Optional[ScanResult] = None
        self.status_text: str = "就绪"
        self.warnings: List[str] = []
        self.selected_platform: str = "all"
        self.search_query: str = ""
        self.starred_only: bool = False

        self.event_queue: "queue.Queue[tuple[str, VolumeInfo]]" = queue.Queue()
        self.is_watching: bool = False
        self._watch_thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None

    def all_saves(self) -> List[SaveEntry]:
        if not self.current_result:
            return []
        return list(self.current_result.saves)

    def platform_counts(self) -> Dict[str, int]:
        counts = {key: 0 for key in PLATFORM_ORDER if key != "all"}
        for save in self.all_saves():
            key = save.platform if save.platform in counts else save.platform
            counts[key] = counts.get(key, 0) + 1
        return counts

    def visible_saves(self) -> List[SaveEntry]:
        saves = self.all_saves()
        if self.selected_platform not in (None, "", "all"):
            saves = [save for save in saves if save.platform == self.selected_platform]
        query = (self.search_query or "").strip().lower()
        if query:
            saves = [
                save
                for save in saves
                if query in (save.display_name or "").lower()
                or query in (save.title_id or "").lower()
                or query in (save.source_id or "").lower()
            ]
        if self.starred_only:
            catalog = load_catalog(self.library_root)
            saves = [
                save
                for save in saves
                if (catalog.games.get(game_key(save)) and catalog.games[game_key(save)].starred)
            ]
        return saves

    def set_search_query(self, query: str) -> None:
        self.search_query = query or ""

    def toggle_starred_only(self) -> bool:
        self.starred_only = not self.starred_only
        return self.starred_only

    def toggle_star(self, entry: SaveEntry) -> bool:
        catalog = load_catalog(self.library_root)
        current = catalog.games.get(game_key(entry))
        starred = not bool(current and current.starred)
        game = set_game_meta(self.library_root, entry, starred=starred)
        self.status_text = "已加入收藏" if game.starred else "已取消收藏"
        return game.starred

    def is_starred(self, entry: SaveEntry) -> bool:
        game = load_catalog(self.library_root).games.get(game_key(entry))
        return bool(game and game.starred)

    def game_note(self, entry: SaveEntry) -> str:
        game = load_catalog(self.library_root).games.get(game_key(entry))
        return game.note if game else ""

    def set_note(self, entry: SaveEntry, note: str) -> None:
        set_game_meta(self.library_root, entry, note=note)
        self.status_text = "已保存备注"

    def collection_stats(self) -> Dict[str, int]:
        return collection_stats(self.library_root)

    def export_version_zip(self, snapshot: Snapshot, zip_path: Union[Path, str]) -> Optional[Path]:
        try:
            exported = export_snapshot_zip(snapshot, self.library_root, Path(zip_path))
        except Exception as e:
            self.warnings.append(f"导出失败: {e}")
            self.status_text = f"导出失败: {e}"
            return None
        self.status_text = f"已导出 ZIP: {exported}"
        return exported

    def grouped_saves(self) -> List[Tuple[str, List[SaveEntry]]]:
        saves = self.visible_saves()
        if not saves:
            return []
        known = [key for key in PLATFORM_ORDER if key != "all"]
        buckets: Dict[str, List[SaveEntry]] = {key: [] for key in known}
        extras: Dict[str, List[SaveEntry]] = {}
        for save in saves:
            if save.platform in buckets:
                buckets[save.platform].append(save)
            else:
                extras.setdefault(save.platform, []).append(save)
        groups: List[Tuple[str, List[SaveEntry]]] = []
        for key in known:
            if buckets[key]:
                groups.append((key, buckets[key]))
        for key, items in extras.items():
            groups.append((key, items))
        return groups

    def set_platform_filter(self, platform: str) -> None:
        self.selected_platform = platform or "all"
        label = PLATFORM_LABELS.get(self.selected_platform, self.selected_platform)
        count = len(self.visible_saves())
        if self.current_result:
            self.status_text = f"{label} · {count} 个存档 | [只读]"

    def import_save(self, entry: SaveEntry) -> Optional[Path]:
        """Backup one save into the versioned local library. Never writes to the source volume."""
        try:
            result = backup_save(entry, self.library_root)
        except Exception as e:
            self.warnings.append(f"备份失败: {e}")
            self.status_text = f"备份失败: {e}"
            return None
        self.last_backup = result
        self.last_import_path = result.path
        if result.is_new:
            self.status_text = f"已保存到本地 · 新版本 {result.snapshot.id}"
        else:
            self.status_text = f"已保存到本地 · 内容未变化，沿用版本 {result.snapshot.id}"
        return result.path

    def import_visible_saves(self) -> List[Path]:
        copied: List[Path] = []
        new_count = 0
        for entry in self.visible_saves():
            dest = self.import_save(entry)
            if dest is not None:
                copied.append(dest)
                if self.last_backup and self.last_backup.is_new:
                    new_count += 1
        if copied:
            self.status_text = f"已备份 {len(copied)} 个存档到本地库（新增 {new_count} 个版本）"
        elif not self.visible_saves():
            self.status_text = "当前没有可备份的存档"
        return copied

    def versions_for_entry(self, entry: SaveEntry) -> List[Snapshot]:
        return versions_for(load_catalog(self.library_root), entry)

    def restore_version(self, snapshot: Snapshot, destination: Union[Path, str]) -> Optional[Path]:
        try:
            restored = restore_snapshot(snapshot, self.library_root, Path(destination))
        except Exception as e:
            self.warnings.append(f"恢复失败: {e}")
            self.status_text = f"恢复失败: {e}"
            return None
        self.status_text = f"已恢复版本 {snapshot.id} 到 {restored}"
        return restored

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

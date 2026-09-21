"""Public AppState facade: view state plus forwarding to session services."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

from .artwork import (
    ArtworkResolution,
    ArtworkService,
    default_model,
    normalize_base_url,
    normalize_protocol,
)
from .backend import StorageBackend
from .backup_jobs import backup_updated_saves as run_backup_updated_saves
from .backup_jobs import run_selected_backups
from .device_registry import DeviceRegistry
from .device_session import DeviceSession
from .enrichment import UNSET as _UNSET
from .enrichment import Enrichment
from .ftp_session import FtpSession, parse_port, resolve_ftp_preset_key
from .ftp_fetch import FtpPullResult
from .identity import GameIdentity, GameIdentityResolver, GameIdentityResult
from .jobs import CancelToken, JobProgress, JobSlot
from .library import (
    BackupResult,
    GameDeletion,
    SaveBackupStatus,
    Snapshot,
    default_library_root,
    hash_tree,  # tests monkeypatch vajsave.app_state.hash_tree
)
from .library_actions import LibraryActions
from .library_import import import_snapshot_zip as _import_snapshot_zip
from .library_versions import SnapshotDeletion, delete_snapshot
from .metadata import GameMetadata, GameMetadataResolver
from .models import SaveEntry, ScanResult, VolumeInfo
from .platforms.catalog import PLATFORM_LABELS, PLATFORM_ORDER
from .platforms.common import IDLE_SCAN_PROGRESS, ScanProgress
from .remote_ftp import FtpProfile, RemoteFtpClient
from .restore_targets import remember_restore_dir, suggested_restore_dir as suggest_restore_dir
from .scan_session import PreparedBackupStatuses, PreparedMountScan, ScanSession
from .scanner import scan
from .settings_store import SettingsStore
from .volume import MountedVolumeProvider, VolumeProvider

# Re-exported so ``from vajsave.app_state import PLATFORM_ORDER, PLATFORM_LABELS``
# and the hash_tree monkeypatch path keep working.
__all__ = [
    "PLATFORM_ORDER",
    "PLATFORM_LABELS",
    "BACKUP_STATUS_LABELS",
    "PreparedMountScan",
    "PreparedBackupStatuses",
    "AppState",
]

BACKUP_STATUS_LABELS = {
    "new": "新",
    "changed": "有变化",
    "unchanged": "已备份",
}


class AppState:
    """Pure Python state machine for the vaj-save Desktop App."""

    def __init__(
        self,
        provider: Optional[VolumeProvider] = None,
        scan_fn: Optional[Callable[[Union[Path, str]], ScanResult]] = None,
        backend: Optional[StorageBackend] = None,
        library_root: Optional[Union[Path, str]] = None,
        ftp_client_factory: Optional[Callable[[FtpProfile], RemoteFtpClient]] = None,
    ) -> None:
        self.settings = SettingsStore()
        self.provider: VolumeProvider = provider or MountedVolumeProvider()
        self.scan_fn: Callable[[Union[Path, str]], ScanResult] = scan_fn or scan
        self.backend: Optional[StorageBackend] = backend
        self.library_root: Path = self._resolve_library_root(library_root)
        (
            self.gba_rom_dir,
            self.nds_rom_dir,
            self.gb_rom_dir,
            self.gbc_rom_dir,
        ) = self._resolve_rom_dirs()
        self.libretro_dir: Optional[Path] = self._resolve_libretro_dir()
        self._identity_resolver: Optional[GameIdentityResolver] = None
        self._metadata_service: Optional[GameMetadataResolver] = None
        self._artwork_service: Optional[ArtworkService] = None
        self.last_import_path: Optional[Path] = None
        self.last_backup: Optional[BackupResult] = None

        # FTP pull settings. Host/port/user are always persisted; the password
        # is written to config.json only when ftp_remember_password is true.
        self._ftp_client_factory = ftp_client_factory
        ftp_config = self.settings.load()
        self.ftp_preset_key: str = resolve_ftp_preset_key(ftp_config)
        self.ftp_host: str = self._coerce_text(ftp_config.get("ftp_host"))
        self.ftp_port: Optional[int] = parse_port(ftp_config.get("ftp_port"))
        self.ftp_user: str = self._coerce_text(ftp_config.get("ftp_user"))
        self.ftp_remember_password: bool = self._coerce_bool(
            ftp_config.get("ftp_remember_password")
        )
        self._ftp_password: str = (
            self._coerce_text(ftp_config.get("ftp_password"))
            if self.ftp_remember_password
            else ""
        )

        # Optional LLM cover disambiguation: only ever consulted for a listing
        # where several *different* titles share a query. Disabled by default
        # and inert without a key. The key is persisted so the setting survives
        # a restart, but is never echoed into status/warning text.
        self.llm_cover_enabled: bool = self._coerce_bool(
            ftp_config.get("llm_cover_enabled")
        )
        self.llm_api_key: str = self._coerce_text(ftp_config.get("llm_api_key"))
        self.llm_protocol: str = normalize_protocol(ftp_config.get("llm_protocol"))
        self.llm_base_url: str = normalize_base_url(
            ftp_config.get("llm_base_url"), self.llm_protocol
        )
        self.llm_model: str = (
            self._coerce_text(ftp_config.get("llm_model"))
            or default_model(self.llm_protocol)
        )
        self.auto_backup_on_insert: bool = self._coerce_bool(
            ftp_config.get("auto_backup_on_insert")
        )
        self.last_restore_dir: Optional[Path] = self._coerce_dir(
            ftp_config.get("last_restore_dir")
        )

        self.volumes: List[VolumeInfo] = []
        self.current_mount: Optional[Path] = None
        self.current_result: Optional[ScanResult] = None
        self._auto_selected_mount: bool = False
        self.status_text: str = "就绪"
        self._progress_lock = threading.Lock()
        self._scan_progress: ScanProgress = IDLE_SCAN_PROGRESS
        self._job_slot = JobSlot()
        self.warnings: List[str] = []
        self.library_mode: bool = False
        self.selected_platform: str = "all"
        self.search_query: str = ""
        self.starred_only: bool = False
        self.hide_unchanged: bool = False
        self._backup_statuses: Dict[str, SaveBackupStatus] = {}

        self.event_queue: "queue.Queue[tuple[str, VolumeInfo]]" = queue.Queue()
        self.is_watching: bool = False
        self._watch_thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None

        self.device_registry = DeviceRegistry(self.settings.config_dir() / "devices.json")
        self.devices = DeviceSession(self)
        self.scans = ScanSession(self)
        self.library_actions = LibraryActions(self)
        self.ftp = FtpSession(self)
        self.enrichment = Enrichment(self)

    @staticmethod
    def _coerce_text(value: object) -> str:
        return "" if value is None else str(value).strip()

    @staticmethod
    def _coerce_bool(value: object) -> bool:
        """Interpret a persisted config value as a boolean (JSON is preferred)."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    @staticmethod
    def _resolve_library_root(library_root: Optional[Union[Path, str]]) -> Path:
        """Explicit argument wins; otherwise the persisted app config, then default."""
        if library_root:
            return Path(library_root).expanduser()
        configured = SettingsStore().load().get("library_root")
        if isinstance(configured, str) and configured.strip():
            return Path(configured).expanduser()
        return default_library_root()

    @staticmethod
    def _coerce_dir(value: object) -> Optional[Path]:
        """Normalise a config/dialog directory value; ``None``/blank means unset."""
        if isinstance(value, Path):
            text = str(value)
        elif isinstance(value, str):
            text = value
        else:
            return None
        text = text.strip()
        return Path(text).expanduser() if text else None

    @staticmethod
    def _resolve_rom_dirs() -> Tuple[
        Optional[Path], Optional[Path], Optional[Path], Optional[Path]
    ]:
        """Read the persisted cartridge ROM directories (invalid values -> None)."""
        config = SettingsStore().load()
        return (
            AppState._coerce_dir(config.get("gba_rom_dir")),
            AppState._coerce_dir(config.get("nds_rom_dir")),
            AppState._coerce_dir(config.get("gb_rom_dir")),
            AppState._coerce_dir(config.get("gbc_rom_dir")),
        )

    @staticmethod
    def _resolve_libretro_dir() -> Optional[Path]:
        """Read the persisted libretro metadata directory (invalid value -> None)."""
        return AppState._coerce_dir(SettingsStore().load().get("libretro_dir"))

    def set_library_root(self, library_root: Union[Path, str]) -> Path:
        return self.library_actions.set_library_root(library_root)

    def set_keep_last(self, value: object) -> Optional[int]:
        return self.library_actions.set_keep_last(value)

    def set_rom_dirs(
        self,
        gba_rom_dir: object = _UNSET,
        nds_rom_dir: object = _UNSET,
        *,
        gb_rom_dir: object = _UNSET,
        gbc_rom_dir: object = _UNSET,
    ) -> Tuple[Optional[Path], Optional[Path]]:
        return self.enrichment.set_rom_dirs(
            gba_rom_dir=gba_rom_dir,
            nds_rom_dir=nds_rom_dir,
            gb_rom_dir=gb_rom_dir,
            gbc_rom_dir=gbc_rom_dir,
        )

    def _build_identity_resolver(self) -> GameIdentityResolver:
        return self.enrichment._build_identity_resolver()

    @property
    def identity_resolver(self) -> GameIdentityResolver:
        return self.enrichment.identity_resolver

    @property
    def metadata_resolver(self) -> GameMetadataResolver:
        return self.enrichment.metadata_resolver

    metadata_service = metadata_resolver

    @property
    def artwork_service(self) -> ArtworkService:
        return self.enrichment.artwork_service

    def llm_log_path(self) -> Path:
        return self.enrichment.llm_log_path()

    def test_llm_cover(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        protocol: Optional[str] = None,
    ) -> Tuple[bool, str]:
        return self.enrichment.test_llm_cover(
            api_key=api_key, base_url=base_url, model=model, protocol=protocol
        )

    def set_llm_cover(
        self,
        enabled: Optional[bool] = None,
        api_key: object = _UNSET,
        base_url: object = _UNSET,
        model: object = _UNSET,
        protocol: object = _UNSET,
    ) -> None:
        return self.enrichment.set_llm_cover(
            enabled=enabled,
            api_key=api_key,
            base_url=base_url,
            model=model,
            protocol=protocol,
        )

    def set_libretro_dir(self, value: object = None) -> Optional[Path]:
        return self.enrichment.set_libretro_dir(value)

    def ftp_presets(self) -> List[FtpProfile]:
        return self.ftp.ftp_presets()

    def current_ftp_preset(self) -> FtpProfile:
        return self.ftp.current_ftp_preset()

    def ftp_cache_dir(self, preset_key: Optional[str] = None) -> Path:
        return self.ftp.ftp_cache_dir(preset_key)

    def set_ftp_preset(self, key: object) -> Optional[str]:
        return self.ftp.set_ftp_preset(key)

    def configure_ftp(
        self,
        host: Optional[object] = None,
        port: Optional[object] = None,
        user: Optional[object] = None,
        password: Optional[str] = None,
        preset_key: Optional[str] = None,
        remember_password: Optional[bool] = None,
    ) -> FtpProfile:
        return self.ftp.configure_ftp(
            host=host,
            port=port,
            user=user,
            password=password,
            preset_key=preset_key,
            remember_password=remember_password,
        )

    def pull_ftp_saves(self, token: object = None) -> FtpPullResult:
        return self.ftp.pull_ftp_saves(token=token)

    def resolve_save_metadata(
        self, entry: SaveEntry, identity: Optional[GameIdentity] = None
    ) -> Optional[GameMetadata]:
        return self.enrichment.resolve_save_metadata(entry, identity=identity)

    def cached_save_metadata(
        self, identity: Optional[GameIdentity]
    ) -> Optional[GameMetadata]:
        return self.enrichment.cached_save_metadata(identity)

    def resolve_save_cover(
        self,
        entry: SaveEntry,
        result: Optional[GameIdentityResult] = None,
        identity: Optional[GameIdentity] = None,
    ) -> ArtworkResolution:
        return self.enrichment.resolve_save_cover(entry, result=result, identity=identity)

    def ensure_save_cover(
        self,
        entry: SaveEntry,
        result: Optional[GameIdentityResult] = None,
        metadata: Optional[GameMetadata] = None,
    ) -> ArtworkResolution:
        return self.enrichment.ensure_save_cover(entry, result=result, metadata=metadata)

    def resolve_save_identity(self, entry: SaveEntry) -> GameIdentityResult:
        return self.enrichment.resolve_save_identity(entry)

    def resolve_identities(
        self, entries: Optional[List[SaveEntry]] = None
    ) -> List[GameIdentityResult]:
        return self.enrichment.resolve_identities(entries)

    def bind_save_identity(
        self,
        entry: SaveEntry,
        identity: Optional[GameIdentity] = None,
        rom_path: Optional[Union[Path, str]] = None,
    ) -> GameIdentity:
        return self.enrichment.bind_save_identity(entry, identity=identity, rom_path=rom_path)

    @staticmethod
    def _library_game_id(entry: SaveEntry) -> Optional[str]:
        return LibraryActions.library_game_id(entry)

    @staticmethod
    def _library_identity_key(entry: SaveEntry) -> Optional[str]:
        return LibraryActions.library_identity_key(entry)

    def _game_id(self, entry: SaveEntry) -> str:
        return self.library_actions.game_id(entry)

    def library_entries(self) -> List[SaveEntry]:
        return self.library_actions.library_entries()

    def set_library_mode(self, enabled: bool) -> bool:
        return self.library_actions.set_library_mode(enabled)

    def all_saves(self) -> List[SaveEntry]:
        return self.library_actions.all_saves()

    def platform_counts(self) -> Dict[str, int]:
        return self.library_actions.platform_counts()

    def visible_saves(self) -> List[SaveEntry]:
        return self.library_actions.visible_saves()

    def save_status(self, entry: SaveEntry) -> SaveBackupStatus:
        return self.library_actions.save_status(entry)

    def backup_status_counts(self) -> Dict[str, int]:
        return self.library_actions.backup_status_counts()

    def toggle_hide_unchanged(self) -> bool:
        return self.library_actions.toggle_hide_unchanged()

    def refresh_backup_statuses(self) -> Dict[str, int]:
        return self.scans.refresh_backup_statuses()

    def set_search_query(self, query: str) -> None:
        return self.library_actions.set_search_query(query)

    def toggle_starred_only(self) -> bool:
        return self.library_actions.toggle_starred_only()

    def toggle_star(self, entry: SaveEntry) -> bool:
        return self.library_actions.toggle_star(entry)

    def is_starred(self, entry: SaveEntry) -> bool:
        return self.library_actions.is_starred(entry)

    def game_note(self, entry: SaveEntry) -> str:
        return self.library_actions.game_note(entry)

    def set_note(self, entry: SaveEntry, note: str) -> None:
        return self.library_actions.set_note(entry, note)

    def delete_library_game(self, entry: SaveEntry) -> GameDeletion:
        return self.library_actions.delete_library_game(entry)

    def delete_library_snapshot(self, entry: SaveEntry, snapshot: Snapshot) -> SnapshotDeletion:
        return delete_snapshot(self.library_root, self._game_id(entry), snapshot.id)

    def collection_stats(self) -> Dict[str, int]:
        return self.library_actions.collection_stats()

    def export_version_zip(self, snapshot: Snapshot, zip_path: Union[Path, str]) -> Optional[Path]:
        return self.library_actions.export_version_zip(snapshot, zip_path)

    def import_snapshot_zip(
        self,
        zip_path: Union[Path, str],
        *,
        attach_game_id: Optional[str] = None,
        new_platform: Optional[str] = None,
        new_title_id: Optional[str] = None,
        new_display_name: Optional[str] = None,
        new_slot: Optional[str] = None,
    ) -> BackupResult:
        return _import_snapshot_zip(
            self.library_root,
            Path(zip_path),
            attach_game_id=attach_game_id,
            new_platform=new_platform,
            new_title_id=new_title_id,
            new_display_name=new_display_name,
            new_slot=new_slot,
        )

    def grouped_saves(self) -> List[Tuple[str, List[SaveEntry]]]:
        return self.library_actions.grouped_saves()

    def set_platform_filter(self, platform: str) -> None:
        return self.library_actions.set_platform_filter(platform)

    def import_save(self, entry: SaveEntry) -> Optional[Path]:
        return self.library_actions.import_save(entry)

    def import_selected_saves(
        self, entries: List[SaveEntry], token: Optional[CancelToken] = None
    ) -> List[Path]:
        return run_selected_backups(self, entries, token)

    def import_visible_saves(self) -> List[Path]:
        return self.import_selected_saves(list(self.visible_saves()))

    def backup_updated_saves(self, token: Optional[CancelToken] = None) -> List[Path]:
        return run_backup_updated_saves(self, token)

    def cancel_job(self) -> None:
        self._job_slot.cancel()

    def job_progress(self) -> JobProgress:
        return self._job_slot.progress()

    def _try_begin_job(self, token: Optional[CancelToken] = None) -> Optional[CancelToken]:
        started = self._job_slot.try_begin(token)
        if started is None:
            self.status_text = "已有任务正在进行"
        return started

    def _end_job(self, token: Optional[CancelToken] = None) -> None:
        self._job_slot.end(token)

    def _report_job_progress(self, progress: JobProgress) -> None:
        self._job_slot.report(progress)
        if progress.message:
            self.status_text = progress.message

    def versions_for_entry(self, entry: SaveEntry) -> List[Snapshot]:
        return self.library_actions.versions_for_entry(entry)

    def restore_version(self, snapshot: Snapshot, destination: Union[Path, str]) -> Optional[Path]:
        restored = self.library_actions.restore_version(snapshot, destination)
        if restored is not None:
            remember_restore_dir(self, Path(destination))
        return restored

    def suggested_restore_dir(self, entry: SaveEntry) -> Optional[Path]:
        return suggest_restore_dir(self, entry)

    def set_auto_backup_on_insert(self, enabled: bool) -> bool:
        self.auto_backup_on_insert = bool(enabled)
        self.settings.update(auto_backup_on_insert=self.auto_backup_on_insert)
        return self.auto_backup_on_insert

    def job_idle(self) -> bool:
        return not self._job_slot.is_running()

    def refresh_volumes(self) -> List[VolumeInfo]:
        return self.devices.refresh_volumes()

    def report_scan_progress(self, progress: ScanProgress) -> None:
        return self.scans.report_scan_progress(progress)

    def scan_progress(self) -> ScanProgress:
        return self.scans.scan_progress()

    def clear_scan_progress(self) -> None:
        return self.scans.clear_scan_progress()

    def begin_mount_scan(self, mount_point: Union[Path, str], auto: bool = False) -> Path:
        return self.scans.begin_mount_scan(mount_point, auto=auto)

    def prepare_mount_scan(
        self, mount_point: Union[Path, str], refresh: bool = False
    ) -> PreparedMountScan:
        return self.scans.prepare_mount_scan(mount_point, refresh=refresh)

    def apply_prepared_mount_scan(self, prepared: PreparedMountScan) -> ScanResult:
        return self.scans.apply_prepared_mount_scan(prepared)

    def prepare_backup_statuses(
        self, entries: Optional[Sequence[SaveEntry]] = None
    ) -> PreparedBackupStatuses:
        return self.scans.prepare_backup_statuses(entries)

    def apply_backup_statuses(self, prepared: PreparedBackupStatuses) -> bool:
        return self.scans.apply_backup_statuses(prepared)

    def select_mount(
        self, mount_point: Union[Path, str], auto: bool = False, refresh: bool = False
    ) -> ScanResult:
        return self.scans.select_mount(mount_point, auto=auto, refresh=refresh)

    def register_custom_path(self, path: Union[Path, str]) -> Path:
        return self.devices.register_custom_path(path)

    def select_custom_path(self, path: Union[Path, str]) -> ScanResult:
        return self.devices.select_custom_path(path)

    def preferred_volume(self) -> Optional[VolumeInfo]:
        return self.devices.preferred_volume()

    def mount_selection_candidate(self) -> Optional[VolumeInfo]:
        return self.devices.mount_selection_candidate()

    def ensure_mount_selected(self) -> Optional[VolumeInfo]:
        return self.devices.ensure_mount_selected()

    def apply_watch_event(
        self, event_type: str, volume: VolumeInfo, *, auto_select: bool = True
    ) -> None:
        return self.devices.apply_watch_event(event_type, volume, auto_select=auto_select)

    def drain_events(self, *, auto_select: bool = True) -> int:
        return self.devices.drain_events(auto_select=auto_select)

    def start_watch(self, interval: float = 1.0) -> None:
        return self.devices.start_watch(interval)

    def stop_watch(self, timeout: float = 1.0) -> None:
        return self.devices.stop_watch(timeout)

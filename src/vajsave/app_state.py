import os
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

from .backend import StorageBackend
from .library import (
    BackupResult,
    Catalog,
    SaveBackupStatus,
    Snapshot,
    classify_save_status,
    collection_stats,
    default_library_root,
    backup_save,
    export_snapshot_zip,
    game_key,
    hash_tree,
    load_app_config,
    load_catalog,
    restore_snapshot,
    save_app_config,
    set_game_meta,
    versions_for,
)
from .artwork import (
    COVER_CACHE_DIR,
    PLACEHOLDER,
    ArtworkResolution,
    ArtworkService,
    CoverCache,
)
from .metadata import (
    METADATA_CACHE_NAME,
    GameMetadata,
    GameMetadataResolver,
    LibretroMetadataProvider,
    MetadataCache,
    default_libretro_dirs,
)
from .identity import (
    BINDINGS_NAME,
    ROM_CACHE_NAME,
    BindingStore,
    GameIdentity,
    GameIdentityResolver,
    GameIdentityResult,
)
from .models import SaveEntry, ScanResult, VolumeInfo
from .scanner import scan
from .volume import MountedVolumeProvider, VolumeProvider, watch_volumes

PLATFORM_ORDER = ["all", "psp", "vita", "switch", "3ds", "nds", "gba"]

# Sentinel distinguishing "leave this setting untouched" from an explicit None
# (which clears a persisted ROM directory) in ``set_rom_dirs``.
_UNSET = object()

# Folders that mean "this mount is a handheld card", so we may search it for ROMs
# even when the volume is not marked removable (e.g. a folder added via 添加设备).
_HANDHELD_ROM_MARKERS = (
    "SAVEGAME",
    "SAVER",
    "SAVES",
    "GBASYS",
    "roms",
    "ROMS",
    "_nds",
)

PLATFORM_LABELS = {
    "all": "全部",
    "psp": "PSP",
    "vita": "PS Vita",
    "switch": "Switch",
    "3ds": "3DS",
    "nds": "NDS",
    "gba": "GBA",
}

BACKUP_STATUS_LABELS = {
    "new": "新",
    "changed": "有变化",
    "unchanged": "已备份",
}


def _build_status_text(result: ScanResult, counts: Optional[Dict[str, int]] = None) -> str:
    """Build a human-readable status text for a ScanResult."""
    if not Path(result.root_path).exists():
        base = f"路径不存在或不可读: {result.root_path}"
    else:
        has_encrypted_3ds = any(
            s.source_id == "3ds_sd" or (s.extra and s.extra.get("encrypted_container"))
            for s in result.sources
        )
        if has_encrypted_3ds and not result.saves:
            base = "平台: 3DS | 发现加密 SD 卡 (Nintendo 3DS)，原生存档已加密，不可直接管理 | [只读]"
        elif result.platform == "unknown" and not result.saves:
            base = "平台: 未知/通用卷 | 发现 0 个可识别存档 | [只读]"
        else:
            source_count = len(result.sources)
            save_count = len(result.saves)
            base = f"平台: {result.platform.upper()} | 来源: {source_count} 个 | 存档: {save_count} 个 | [只读]"

    if counts is None:
        return base
    return (
        f"{base} | 新 {counts.get('new', 0)} · "
        f"有变化 {counts.get('changed', 0)} · "
        f"已备份 {counts.get('unchanged', 0)}"
    )


def _mount_sort_key(volume: VolumeInfo) -> Tuple[int, int]:
    """Ranking key for auto-selecting a device.

    Removable volumes (USB sticks, handhelds exposing themselves as UMS drives)
    come before fixed disks, and inside a group the highest Windows drive letter
    wins, because a freshly attached device is normally handed the next free
    letter. Volumes without a drive letter keep their provider order.
    """
    text = str(volume.mount_point)
    letter = -1
    if len(text) >= 2 and text[1] == ":":
        char = text[0].upper()
        if "A" <= char <= "Z":
            letter = ord(char)
    return (0 if volume.is_removable else 1, -letter)


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
        self.library_root: Path = self._resolve_library_root(library_root)
        self.gba_rom_dir, self.nds_rom_dir = self._resolve_rom_dirs()
        self.libretro_dir: Optional[Path] = self._resolve_libretro_dir()
        self._identity_resolver: Optional[GameIdentityResolver] = None
        self._metadata_service: Optional[GameMetadataResolver] = None
        self._artwork_service: Optional[ArtworkService] = None
        self.last_import_path: Optional[Path] = None
        self.last_backup: Optional[BackupResult] = None

        self.volumes: List[VolumeInfo] = []
        self.current_mount: Optional[Path] = None
        self.current_result: Optional[ScanResult] = None
        self._auto_selected_mount: bool = False
        self.status_text: str = "就绪"
        self.warnings: List[str] = []
        self.selected_platform: str = "all"
        self.search_query: str = ""
        self.starred_only: bool = False
        # Backwards-compatible filter flag: False shows every save (new/changed/
        # unchanged); True is the "仅显示有更新" filter and hides unchanged rows.
        self.hide_unchanged: bool = False
        self._backup_statuses: Dict[str, SaveBackupStatus] = {}

        self.event_queue: "queue.Queue[tuple[str, VolumeInfo]]" = queue.Queue()
        self.is_watching: bool = False
        self._watch_thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None

    @staticmethod
    def _resolve_library_root(library_root: Optional[Union[Path, str]]) -> Path:
        """Explicit argument wins; otherwise the persisted app config, then default."""
        if library_root:
            return Path(library_root).expanduser()
        configured = load_app_config().get("library_root")
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
    def _resolve_rom_dirs() -> Tuple[Optional[Path], Optional[Path]]:
        """Read the persisted GBA/NDS ROM directories (invalid values -> None)."""
        config = load_app_config()
        return (
            AppState._coerce_dir(config.get("gba_rom_dir")),
            AppState._coerce_dir(config.get("nds_rom_dir")),
        )

    @staticmethod
    def _resolve_libretro_dir() -> Optional[Path]:
        """Read the persisted libretro metadata directory (invalid value -> None)."""
        return AppState._coerce_dir(load_app_config().get("libretro_dir"))

    def set_library_root(self, library_root: Union[Path, str]) -> Path:
        """Switch the local backup library and persist it to the app config.

        Clears the cached backup statuses; when a scan result is present the
        statuses are recomputed against the new library.
        """
        self.library_root = Path(library_root).expanduser()
        self._backup_statuses = {}
        self._identity_resolver = None
        # All metadata/artwork caches live under the library root.
        self._metadata_service = None
        self._artwork_service = None
        config = load_app_config()
        config["library_root"] = str(self.library_root)
        save_app_config(config)
        if self.current_result is not None:
            self.refresh_backup_statuses()
            self.status_text = _build_status_text(
                self.current_result, counts=self.backup_status_counts()
            )
        else:
            self.status_text = f"备份库路径已更新: {self.library_root}"
        return self.library_root

    def set_rom_dirs(
        self,
        gba_rom_dir: object = _UNSET,
        nds_rom_dir: object = _UNSET,
    ) -> Tuple[Optional[Path], Optional[Path]]:
        """Persist the GBA/NDS ROM directories used for cartridge identity.

        Only the arguments that are passed are touched: the default sentinel
        keeps the current value, ``None``/blank clears it.  Returns the new
        ``(gba_rom_dir, nds_rom_dir)`` pair.
        """
        config = load_app_config()
        if gba_rom_dir is not _UNSET:
            self.gba_rom_dir = self._coerce_dir(gba_rom_dir)
            if self.gba_rom_dir is not None:
                config["gba_rom_dir"] = str(self.gba_rom_dir)
            else:
                config.pop("gba_rom_dir", None)
        if nds_rom_dir is not _UNSET:
            self.nds_rom_dir = self._coerce_dir(nds_rom_dir)
            if self.nds_rom_dir is not None:
                config["nds_rom_dir"] = str(self.nds_rom_dir)
            else:
                config.pop("nds_rom_dir", None)
        save_app_config(config)
        self._identity_resolver = None
        self._metadata_service = None
        return (self.gba_rom_dir, self.nds_rom_dir)

    def _should_search_volume_for_roms(self, root: Path) -> bool:
        """True when ``root`` is a handheld card, not the OS system drive."""
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if sys.platform == "win32":
            system = os.environ.get("SystemDrive", "C:").rstrip("\\").upper()
            drive = (resolved.drive or "").rstrip("\\").upper()
            if drive and drive == system:
                return False
        else:
            posix = resolved.as_posix()
            if posix in ("/", "/System", "/Applications"):
                return False
        for volume in self.volumes:
            try:
                if Path(volume.mount_point).resolve() == resolved and volume.is_removable:
                    return True
            except OSError:
                if Path(volume.mount_point) == root and volume.is_removable:
                    return True
        for name in _HANDHELD_ROM_MARKERS:
            try:
                if (resolved / name).is_dir():
                    return True
            except OSError:
                continue
        return False

    def _volume_rom_search_roots(self) -> List[Path]:
        if self.current_mount is None:
            return []
        try:
            root = Path(self.current_mount).expanduser()
            if not root.is_dir():
                return []
        except OSError:
            return []
        if not self._should_search_volume_for_roms(root):
            return []
        return [root]

    def _build_identity_resolver(self) -> GameIdentityResolver:
        extra = self._volume_rom_search_roots()
        rom_dirs: Dict[str, List[Path]] = {}
        gba_roots = ([self.gba_rom_dir] if self.gba_rom_dir is not None else []) + extra
        nds_roots = ([self.nds_rom_dir] if self.nds_rom_dir is not None else []) + extra
        if gba_roots:
            rom_dirs["gba"] = gba_roots
        if nds_roots:
            rom_dirs["nds"] = nds_roots
        bindings = BindingStore(self.library_root / BINDINGS_NAME)
        return GameIdentityResolver(
            rom_dirs=rom_dirs,
            bindings=bindings,
            cache_path=self.library_root / ROM_CACHE_NAME,
        )

    @property
    def identity_resolver(self) -> GameIdentityResolver:
        if self._identity_resolver is None:
            self._identity_resolver = self._build_identity_resolver()
        return self._identity_resolver

    # -- metadata / artwork --------------------------------------------------

    def _libretro_dirs(self) -> List[Path]:
        return default_libretro_dirs(
            self.library_root, configured=load_app_config().get("libretro_dir")
        )

    @property
    def metadata_resolver(self) -> GameMetadataResolver:
        """Cache-first metadata resolver backed by the local libretro index."""
        if self._metadata_service is None:
            provider = LibretroMetadataProvider(self._libretro_dirs())
            cache = MetadataCache(self.library_root / METADATA_CACHE_NAME)
            self._metadata_service = GameMetadataResolver(provider, cache)
        return self._metadata_service

    # Back-compat alias for callers written before the resolver was renamed.
    metadata_service = metadata_resolver

    @property
    def artwork_service(self) -> ArtworkService:
        """Cover provider/downloader bound to the library's ``covers/`` tree."""
        if self._artwork_service is None:
            cache = CoverCache(self.library_root / COVER_CACHE_DIR)
            self._artwork_service = ArtworkService(cache=cache)
        return self._artwork_service

    def set_libretro_dir(self, value: object = None) -> Optional[Path]:
        """Persist (or clear) the optional libretro ``.dat`` directory."""
        config = load_app_config()
        resolved = self._coerce_dir(value)
        if resolved is not None:
            config["libretro_dir"] = str(resolved)
        else:
            config.pop("libretro_dir", None)
        save_app_config(config)
        self.libretro_dir = resolved
        self._metadata_service = None
        return resolved

    def resolve_save_metadata(
        self, entry: SaveEntry, identity: Optional[GameIdentity] = None
    ) -> Optional[GameMetadata]:
        """Canonical metadata for a save's ROM, or ``None``. Never raises."""
        try:
            if identity is None:
                identity = self.resolve_save_identity(entry).identity
            if identity is None:
                return None
            return self.metadata_resolver.resolve(identity)
        except Exception:  # noqa: BLE001 - metadata is best-effort
            return None

    def cached_save_metadata(
        self, identity: Optional[GameIdentity]
    ) -> Optional[GameMetadata]:
        """Cache-only metadata (no provider access) for the UI hot path."""
        if identity is None:
            return None
        try:
            return self.metadata_resolver.cached(identity)
        except Exception:  # noqa: BLE001
            return None

    def resolve_save_cover(
        self,
        entry: SaveEntry,
        result: Optional[GameIdentityResult] = None,
        identity: Optional[GameIdentity] = None,
    ) -> ArtworkResolution:
        """Network-free best cover (user > downloaded cache > embedded)."""
        try:
            if identity is None:
                identity = (result.identity if result else None)
            if identity is None:
                identity = self.resolve_save_identity(entry).identity
            key = identity.identity_key if identity is not None else None
            return self.artwork_service.resolve(
                entry,
                self.library_root,
                identity_key=key,
                platform=getattr(entry, "platform", None),
            )
        except Exception:  # noqa: BLE001 - a cover must never break the UI
            return PLACEHOLDER

    def ensure_save_cover(
        self,
        entry: SaveEntry,
        result: Optional[GameIdentityResult] = None,
        metadata: Optional[GameMetadata] = None,
    ) -> ArtworkResolution:
        """Full fallback order including a download attempt (worker-thread safe).

        The official libretro filename rule needs the index's canonical title, so
        without metadata this stays a local-only resolution: the app never guesses
        a name (and never touches the network) for an unknown ROM.
        """
        try:
            if metadata is None or not metadata.canonical_title:
                return self.resolve_save_cover(entry, result=result)
            if result is None:
                result = self.resolve_save_identity(entry)
            identity = result.identity
            key = identity.identity_key if identity is not None else None
            return self.artwork_service.ensure_cover(
                entry,
                platform=getattr(entry, "platform", "") or "",
                metadata=metadata,
                identity_key=key,
                library_root=self.library_root,
            )
        except Exception:  # noqa: BLE001
            return PLACEHOLDER

    def resolve_save_identity(self, entry: SaveEntry) -> GameIdentityResult:
        return self.identity_resolver.resolve(entry)

    def resolve_identities(
        self, entries: Optional[List[SaveEntry]] = None
    ) -> List[GameIdentityResult]:
        target = list(entries) if entries is not None else self.all_saves()
        return self.identity_resolver.resolve_many(target)

    def bind_save_identity(
        self,
        entry: SaveEntry,
        identity: Optional[GameIdentity] = None,
        rom_path: Optional[Union[Path, str]] = None,
    ) -> GameIdentity:
        return self.identity_resolver.bind(entry, identity=identity, rom_path=rom_path)

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
        # Filter combination is unchanged: new + changed stay visible, only
        # unchanged rows are dropped when "仅显示有更新" is active.
        if self.hide_unchanged:
            saves = [save for save in saves if self.save_status(save).status != "unchanged"]
        return saves

    def save_status(self, entry: SaveEntry) -> SaveBackupStatus:
        cached = self._backup_statuses.get(entry.path)
        if cached is not None:
            return cached
        status = classify_save_status(entry, load_catalog(self.library_root))
        self._backup_statuses[entry.path] = status
        return status

    def backup_status_counts(self) -> Dict[str, int]:
        counts = {"new": 0, "changed": 0, "unchanged": 0}
        for save in self.all_saves():
            key = self.save_status(save).status
            if key not in counts:
                counts[key] = 0
            counts[key] += 1
        return counts

    def toggle_hide_unchanged(self) -> bool:
        """Toggle hide_unchanged; returns the new hide_unchanged value."""
        self.hide_unchanged = not self.hide_unchanged
        return self.hide_unchanged

    def _refresh_entry_status(
        self, entry: SaveEntry, catalog: Optional[Catalog] = None
    ) -> SaveBackupStatus:
        catalog = catalog if catalog is not None else load_catalog(self.library_root)
        hash_error = False
        digest: Optional[str] = None
        try:
            digest = hash_tree(Path(entry.path))
        except (OSError, ValueError, FileNotFoundError) as e:
            hash_error = True
            self.warnings.append(f"计算存档哈希失败: {entry.display_name or entry.path}: {e}")
        status = classify_save_status(
            entry,
            catalog,
            digest=digest,
            hash_error=hash_error,
        )
        self._backup_statuses[entry.path] = status
        return status

    def refresh_backup_statuses(self) -> Dict[str, int]:
        """Recompute and cache status for every scanned save."""
        self._backup_statuses = {}
        catalog = load_catalog(self.library_root)
        pending: List[SaveEntry] = []
        for entry in self.all_saves():
            game = catalog.games.get(game_key(entry))
            if not game or not game.versions:
                self._backup_statuses[entry.path] = classify_save_status(entry, catalog, digest=None)
            else:
                pending.append(entry)
        if pending:
            def _hash_one(item: SaveEntry) -> Tuple[str, Optional[str], Optional[BaseException]]:
                try:
                    return item.path, hash_tree(Path(item.path)), None
                except (OSError, ValueError, FileNotFoundError) as exc:
                    return item.path, None, exc

            workers = min(8, len(pending))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                hashed = list(pool.map(_hash_one, pending))
            by_path = {path: (digest, err) for path, digest, err in hashed}
            for entry in pending:
                digest, err = by_path[entry.path]
                if err is not None:
                    self.warnings.append(
                        f"计算存档哈希失败: {entry.display_name or entry.path}: {err}"
                    )
                    self._backup_statuses[entry.path] = classify_save_status(
                        entry, catalog, hash_error=True
                    )
                else:
                    self._backup_statuses[entry.path] = classify_save_status(
                        entry, catalog, digest=digest
                    )
        return self.backup_status_counts()

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
        # Refresh this row so identical content becomes unchanged (and may hide).
        self._refresh_entry_status(entry)
        if result.is_new:
            self.status_text = f"已保存到本地 · 新版本 {result.snapshot.id}"
        else:
            self.status_text = f"已保存到本地 · 内容未变化，沿用版本 {result.snapshot.id}"
        return result.path

    def import_selected_saves(self, entries: List[SaveEntry]) -> List[Path]:
        """Backup explicit multi-selection; skips nothing the caller passed."""
        copied: List[Path] = []
        new_count = 0
        for entry in entries:
            dest = self.import_save(entry)
            if dest is not None:
                copied.append(dest)
                if self.last_backup and self.last_backup.is_new:
                    new_count += 1
        if copied:
            self.status_text = f"已备份 {len(copied)} 个存档到本地库（新增 {new_count} 个版本）"
        elif not entries:
            self.status_text = "当前没有可备份的存档"
        return copied

    def import_visible_saves(self) -> List[Path]:
        # Snapshot first so hide_unchanged after each backup does not shrink the work list mid-loop.
        targets = list(self.visible_saves())
        return self.import_selected_saves(targets)

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

    def select_mount(self, mount_point: Union[Path, str], auto: bool = False) -> ScanResult:
        """Select a volume or path to scan and update current result.

        ``auto`` marks a selection the app made on the user's behalf; only those
        may later be replaced by a better device (see ``ensure_mount_selected``).
        """
        path = Path(mount_point)
        self.current_mount = path
        self._auto_selected_mount = auto
        self._identity_resolver = None
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
        self.refresh_backup_statuses()
        self.status_text = _build_status_text(res, counts=self.backup_status_counts())
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

    def preferred_volume(self) -> Optional[VolumeInfo]:
        """The device the app should default to, or None when nothing is mounted."""
        if not self.volumes:
            return None
        return min(self.volumes, key=_mount_sort_key)

    def ensure_mount_selected(self) -> Optional[VolumeInfo]:
        """Pick a default device when none is chosen, or upgrade an automatic choice.

        Called after enumerating volumes so a just-attached USB stick or handheld
        on a high drive letter (F:) is preferred over built-in C:/D:/E: drives.
        A device the user picked themselves is never replaced.
        """
        preferred = self.preferred_volume()
        if preferred is None:
            return None
        if self.current_mount is not None:
            if not self._auto_selected_mount:
                return None
            if Path(preferred.mount_point) == Path(self.current_mount):
                return None
        self.select_mount(preferred.mount_point, auto=True)
        return preferred

    def apply_watch_event(self, event_type: str, volume: VolumeInfo) -> None:
        """Handle volume appearance or disappearance."""
        v_mount = Path(volume.mount_point)
        if event_type == "appeared":
            idx = next((i for i, v in enumerate(self.volumes) if v.mount_point == v_mount), None)
            if idx is None:
                self.volumes.append(volume)
            else:
                self.volumes[idx] = volume

            # Re-evaluate instead of accepting whichever volume arrived first, so
            # the startup burst of events settles on the preferred device.
            before = self.current_mount
            self.ensure_mount_selected()
            if self.current_mount == before:
                self.status_text = f"发现新设备: {volume.name}"

        elif event_type == "disappeared":
            self.volumes = [v for v in self.volumes if v.mount_point != v_mount]
            if self.current_mount == v_mount:
                self.current_mount = None
                self.current_result = None
                self._auto_selected_mount = False
                self._backup_statuses = {}
                self.warnings = []
                self.status_text = f"卷 {volume.name} 已卸载"
                # Fall back to another attached device instead of showing nothing.
                self.ensure_mount_selected()
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

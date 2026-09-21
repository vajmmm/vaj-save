import os
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

from .backend import StorageBackend
from .library import (
    BackupResult,
    Catalog,
    GameDeletion,
    SaveBackupStatus,
    Snapshot,
    catalog_entries,
    classify_save_status,
    collection_stats,
    default_library_root,
    backup_save,
    delete_game,
    export_snapshot_zip,
    game_key,
    hash_tree,
    is_inside_library,
    load_app_config,
    load_catalog,
    latest_snapshot,
    path_mtime_iso,
    parse_keep_last,
    restore_snapshot,
    save_app_config,
    save_keep_last,
    set_game_meta,
    versions_for,
)
from .artwork import (
    COVER_CACHE_DIR,
    PLACEHOLDER,
    ArtworkResolution,
    ArtworkService,
    CoverCache,
    LLMCoverChooser,
    LLM_LOG_NAME,
    default_base_url,
    default_model,
    normalize_base_url,
    normalize_protocol,
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
    resolved,
)
from .models import SaveEntry, ScanResult, VolumeInfo
from .ftp_fetch import FtpPullResult, cache_dir_for, ftp_cache_root, pull_preset
from .remote_ftp import (
    DEFAULT_PRESET_KEY,
    FtpProfile,
    RemoteFtpClient,
    get_preset,
    preset_keys,
    presets,
)
from .scanner import scan
from .volume import MountedVolumeProvider, VolumeProvider, watch_volumes

PLATFORM_ORDER = ["all", "psp", "vita", "switch", "3ds", "nds", "gba"]

# Platforms whose libretro provider name is the save's own SFO / display title
# rather than a ROM-digest index canonical title.
_TITLE_PROVIDER_PLATFORMS = ("psp", "vita", "3ds")

# Sentinel distinguishing "leave this setting untouched" from an explicit None
# (which clears a persisted ROM directory) in ``set_rom_dirs``.
_UNSET = object()


def _parse_port(value: object) -> Optional[int]:
    """Coerce a config/dialog value to a valid TCP port; invalid values -> None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 65535 else None
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    port = int(text)
    return port if 1 <= port <= 65535 else None


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


@dataclass(frozen=True)
class PreparedMountScan:
    """后台扫描产生、等待由 UI 主线程原子提交的结果。"""

    mount_point: Path
    result: ScanResult
    backup_statuses: Dict[str, SaveBackupStatus]
    status_warnings: Tuple[str, ...]
    status_counts: Dict[str, int]
    status_text: str


@dataclass(frozen=True)
class PreparedBackupStatuses:
    """第二阶段后台哈希产生、等待由 UI 主线程提交的结果。"""

    statuses: Dict[str, SaveBackupStatus]
    status_warnings: Tuple[str, ...]
    status_counts: Dict[str, int]
    status_text: Optional[str] = None
    entries: Tuple[SaveEntry, ...] = ()
    mount_point: Optional[Path] = None


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

        # FTP pull settings. The host/port/user are persisted so a pull can be
        # repeated; the password deliberately stays in memory only.
        self._ftp_client_factory = ftp_client_factory
        ftp_config = load_app_config()
        self.ftp_preset_key: str = self._resolve_ftp_preset_key(ftp_config)
        self.ftp_host: str = self._coerce_text(ftp_config.get("ftp_host"))
        self.ftp_port: Optional[int] = _parse_port(ftp_config.get("ftp_port"))
        self.ftp_user: str = self._coerce_text(ftp_config.get("ftp_user"))
        self._ftp_password: str = ""

        # Optional LLM cover disambiguation: only ever consulted for a listing
        # where several *different* titles share a query. Disabled by default
        # and inert without a key. The key is persisted so the setting survives
        # a restart, but is never echoed into status/warning text.
        self.llm_cover_enabled: bool = self._coerce_bool(
            ftp_config.get("llm_cover_enabled")
        )
        self.llm_api_key: str = self._coerce_text(ftp_config.get("llm_api_key"))
        # The OpenAI-compatible endpoint and model are persisted with the key so
        # a self-hosted gateway or a different model survives a restart. Blank or
        # missing values fall back to the built-in defaults. The protocol selects
        # the wire shape (OpenAI chat-completions or Anthropic messages); it
        # defaults to OpenAI and any unknown value (e.g. Gemini) collapses to it.
        self.llm_protocol: str = normalize_protocol(ftp_config.get("llm_protocol"))
        # Blank endpoints fall back to the chosen protocol's built-in default;
        # a bare host without a ``/v1`` segment is completed so the request
        # path resolves.
        self.llm_base_url: str = normalize_base_url(
            ftp_config.get("llm_base_url"), self.llm_protocol
        )
        self.llm_model: str = (
            self._coerce_text(ftp_config.get("llm_model"))
            or default_model(self.llm_protocol)
        )

        self.volumes: List[VolumeInfo] = []
        self.current_mount: Optional[Path] = None
        self.current_result: Optional[ScanResult] = None
        self._auto_selected_mount: bool = False
        self.status_text: str = "就绪"
        self.warnings: List[str] = []
        # Browse source: False = the selected device, True = the local library
        # catalog (cross-platform, one row per game).
        self.library_mode: bool = False
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
    def _resolve_ftp_preset_key(config: Dict) -> str:
        configured = AppState._coerce_text(config.get("ftp_preset")).lower()
        return configured if configured in preset_keys() else DEFAULT_PRESET_KEY

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

    def set_keep_last(self, value: object) -> Optional[int]:
        """Persist the per-library ``keep_last`` (0 = unlimited).

        Accepts an ``int`` or a base-10 digit string (the settings entry text);
        anything else is rejected with ``None`` and ``settings.json`` is left
        untouched. Changing the setting never prunes existing versions -- pruning
        still happens only when a new version is added.
        """
        parsed = parse_keep_last(value)
        if parsed is None:
            return None
        if not save_keep_last(self.library_root, parsed):
            return None
        self.status_text = (
            "保留版本数已设为不限制" if parsed == 0 else f"保留版本数已设为 {parsed}"
        )
        return parsed

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
        for name in _HANDHELD_ROM_MARKERS:
            try:
                if (resolved / name).is_dir():
                    return True
            except OSError:
                continue
        if self.current_result is not None:
            if self.current_result.platform in ("gba", "nds"):
                return True
            if any(s.platform in ("gba", "nds") for s in self.current_result.sources):
                return True
            if any(s.platform in ("gba", "nds") for s in self.current_result.saves):
                return True
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
            self._artwork_service = ArtworkService(
                cache=cache, llm_chooser=self._build_llm_chooser()
            )
        return self._artwork_service

    def _build_llm_chooser(self):
        """The optional cover chooser, or ``None`` when disabled/unconfigured."""
        if not self.llm_cover_enabled or not self.llm_api_key:
            return None
        return LLMCoverChooser(
            api_key=self.llm_api_key,
            model=self.llm_model,
            base_url=self.llm_base_url,
            protocol=self.llm_protocol,
            log_path=self.llm_log_path(),
        )

    def llm_log_path(self) -> Path:
        """Where the optional LLM request/result debug log is written."""
        return Path(self.library_root) / LLM_LOG_NAME

    def test_llm_cover(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        protocol: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Send one tiny message with the current settings; never raises.

        Works whether or not the feature is enabled so the user can verify the
        endpoint/model/key before turning it on. The keyword arguments let the
        settings dialog test values that have not been saved yet; ``None`` keeps
        the stored value. Returns ``(ok, detail)`` with a short, key-free
        message suitable for the status bar.
        """
        key = self.llm_api_key if api_key is None else str(api_key).strip()
        if not key:
            return False, "请先填写 API 密钥"
        chooser = LLMCoverChooser(
            api_key=key,
            model=self.llm_model if model is None else model,
            base_url=self.llm_base_url if base_url is None else base_url,
            protocol=self.llm_protocol if protocol is None else protocol,
            log_path=self.llm_log_path(),
        )
        try:
            return chooser.probe()
        except Exception as exc:  # noqa: BLE001 - a test must never crash the UI
            return False, f"测试失败: {type(exc).__name__}"

    @staticmethod
    def _follow_protocol_default(
        value: object, old_default: str, new_default: str
    ) -> str:
        """The new protocol's default when ``value`` is blank/old-default.

        A customised endpoint/model is preserved across a protocol switch; only
        a value that is still the old protocol's built-in default follows along.
        """
        text = "" if value is None else str(value).strip()
        if not text or text.rstrip("/") == old_default.rstrip("/"):
            return new_default
        return text

    def set_llm_cover(
        self,
        enabled: Optional[bool] = None,
        api_key: object = _UNSET,
        base_url: object = _UNSET,
        model: object = _UNSET,
        protocol: object = _UNSET,
    ) -> None:
        """Persist the optional LLM cover-disambiguation settings.

        ``enabled`` toggles the feature, ``api_key`` (when passed) replaces the
        stored key, and ``base_url``/``model`` (when passed) replace the stored
        endpoint/model. ``protocol`` selects the wire shape
        (``openai-completions`` or ``anthropic-messages``; the pre-rename
        ``openai``/``anthropic`` values are still accepted); switching it
        rewrites a still-default base URL (and model) to the new protocol's
        default while leaving a customised value untouched. A blank base URL
        falls back to the protocol's built-in default, and a bare host without a
        ``/v1`` segment is completed. The key is written to ``config.json`` so it
        survives a restart but is deliberately never copied into ``status_text``
        or ``warnings``. The cached artwork service is dropped so the new chooser
        takes effect on the next cover lookup.
        """
        if protocol is not _UNSET:
            new_protocol = normalize_protocol(protocol)
            if new_protocol != self.llm_protocol:
                # The settings dialog always sends the current field text, so a
                # protocol switch considers the *incoming* value when one was
                # given, else the stored value. Only the old default follows.
                current_base = self.llm_base_url if base_url is _UNSET else base_url
                current_model = self.llm_model if model is _UNSET else model
                base_url = self._follow_protocol_default(
                    current_base,
                    default_base_url(self.llm_protocol),
                    default_base_url(new_protocol),
                )
                model = self._follow_protocol_default(
                    current_model,
                    default_model(self.llm_protocol),
                    default_model(new_protocol),
                )
                self.llm_protocol = new_protocol
        if enabled is not None:
            self.llm_cover_enabled = bool(enabled)
        if api_key is not _UNSET:
            self.llm_api_key = self._coerce_text(api_key)
        if base_url is not _UNSET:
            self.llm_base_url = normalize_base_url(base_url, self.llm_protocol)
        if model is not _UNSET:
            self.llm_model = self._coerce_text(model) or default_model(
                self.llm_protocol
            )
        config = load_app_config()
        config["llm_cover_enabled"] = self.llm_cover_enabled
        if self.llm_api_key:
            config["llm_api_key"] = self.llm_api_key
        else:
            config.pop("llm_api_key", None)
        config["llm_protocol"] = self.llm_protocol
        config["llm_base_url"] = self.llm_base_url
        config["llm_model"] = self.llm_model
        save_app_config(config)
        self._artwork_service = None

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

    # -- FTP pull ------------------------------------------------------------

    def ftp_presets(self) -> List[FtpProfile]:
        """Built-in presets with the persisted host/port/user overrides applied."""
        return [self._ftp_profile(preset) for preset in presets()]

    def _ftp_profile(self, preset: FtpProfile) -> FtpProfile:
        overrides = {}
        if self.ftp_host:
            overrides["host"] = self.ftp_host
        if self.ftp_port:
            overrides["port"] = self.ftp_port
        if self.ftp_user:
            overrides["user"] = self.ftp_user
        if self._ftp_password:
            overrides["password"] = self._ftp_password
        return replace(preset, **overrides) if overrides else preset

    def current_ftp_preset(self) -> FtpProfile:
        """The active preset (Checkpoint by default)."""
        return self._ftp_profile(get_preset(self.ftp_preset_key))

    def ftp_cache_dir(self, preset_key: Optional[str] = None) -> Path:
        """Local cache directory that a preset is pulled into."""
        return cache_dir_for(self.library_root, preset_key or self.ftp_preset_key)

    def set_ftp_preset(self, key: object) -> Optional[str]:
        """Persist the active FTP preset; unknown keys are rejected with ``None``."""
        text = str(key or "").strip().lower()
        if text not in preset_keys():
            return None
        self.ftp_preset_key = text
        config = load_app_config()
        config["ftp_preset"] = text
        save_app_config(config)
        self.status_text = f"FTP 预设已切换: {get_preset(text).label}"
        return text

    def configure_ftp(
        self,
        host: Optional[object] = None,
        port: Optional[object] = None,
        user: Optional[object] = None,
        password: Optional[str] = None,
        preset_key: Optional[str] = None,
    ) -> FtpProfile:
        """Update FTP connection settings.

        ``host``/``port``/``user`` are persisted; the password is intentionally
        kept in memory only so it is never written to ``config.json``.
        """
        if preset_key is not None:
            self.set_ftp_preset(preset_key)
        if host is not None:
            self.ftp_host = str(host).strip()
        if port is not None:
            parsed = _parse_port(port)
            if parsed is not None:
                self.ftp_port = parsed
        if user is not None:
            self.ftp_user = str(user).strip()
        if password is not None:
            self._ftp_password = str(password)
        config = load_app_config()
        config["ftp_host"] = self.ftp_host
        config["ftp_port"] = self.ftp_port
        config["ftp_user"] = self.ftp_user
        save_app_config(config)
        return self.current_ftp_preset()

    def _register_ftp_volume(self, profile: FtpProfile, path: Union[Path, str]) -> VolumeInfo:
        """Expose a pulled cache directory as a non-removable device row."""
        mount = Path(path)
        volume = VolumeInfo(
            name=f"FTP · {profile.label}",
            mount_point=mount,
            is_removable=False,
            extra={"ftp": True, "ftp_preset": profile.key},
        )
        for index, existing in enumerate(self.volumes):
            if Path(existing.mount_point) == mount:
                self.volumes[index] = volume
                return volume
        self.volumes.append(volume)
        return volume

    def pull_ftp_saves(self) -> FtpPullResult:
        """Pull the active preset into its cache and scan it as a device.

        A failed pull is reported as a warning and leaves the current device
        selection alone, so a half-populated cache is never shown as a device.
        """
        profile = self.current_ftp_preset()
        result = pull_preset(
            profile,
            ftp_cache_root(self.library_root),
            client_factory=self._ftp_client_factory,
        )
        if not result.ok or result.path is None:
            message = result.error or "未知错误"
            self.warnings.append(f"FTP 拉取失败: {message}")
            self.status_text = f"FTP 拉取失败 · {message}"
            return result
        self._register_ftp_volume(profile, result.path)
        self.select_mount(result.path)
        self.status_text = (
            f"FTP 已拉取 · {profile.label} · {result.files} 个文件 | [只读]"
        )
        return result

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
        """Network-free best portrait cover (user > downloaded > embedded)."""
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
        a name (and never touches the network) for an unknown ROM.  PSP/Vita/3DS
        are the exception: they have no ROM index, so their PARAM.SFO / Checkpoint
        display title is used directly. Their small portrait/near-square embedded
        icon remains the fallback when a full-size cover cannot be downloaded;
        landscape PSP banners are ignored.
        """
        try:
            if metadata is None or not metadata.canonical_title:
                identity = result.identity if result is not None else None
                if (
                    identity is not None
                    and (identity.platform or "").strip().lower()
                    in _TITLE_PROVIDER_PLATFORMS
                    and (
                        (identity.title or "").strip()
                        or (identity.title_id or "").strip()
                    )
                ):
                    return self.artwork_service.ensure_cover_for_title(
                        entry,
                        platform=identity.platform,
                        title=identity.title,
                        identity_key=identity.identity_key,
                        library_root=self.library_root,
                        title_id=identity.title_id,
                    )
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
        game_id = self._library_game_id(entry)
        if game_id is not None:
            # A catalog row already knows which game it is; never re-resolve it
            # against a device or trigger a ROM lookup. The persisted ROM
            # identity_key (when present) is what the cover cache is keyed by;
            # without one we fall back to the catalog id, which misses the
            # identity-hash cache and keeps the placeholder.
            identity = GameIdentity(
                identity_key=self._library_identity_key(entry) or game_id,
                platform=getattr(entry, "platform", "") or "unknown",
                title=entry.display_name or game_id,
                title_id=entry.title_id or None,
            )
            return resolved(identity, reason="library", save_path=entry.path)
        return self.identity_resolver.resolve(entry)

    def resolve_identities(
        self, entries: Optional[List[SaveEntry]] = None
    ) -> List[GameIdentityResult]:
        target = list(entries) if entries is not None else self.all_saves()
        if self.library_mode:
            return [self.resolve_save_identity(entry) for entry in target]
        return self.identity_resolver.resolve_many(target)

    def bind_save_identity(
        self,
        entry: SaveEntry,
        identity: Optional[GameIdentity] = None,
        rom_path: Optional[Union[Path, str]] = None,
    ) -> GameIdentity:
        return self.identity_resolver.bind(entry, identity=identity, rom_path=rom_path)

    # -- local library browse mode ------------------------------------------

    @staticmethod
    def _library_game_id(entry: SaveEntry) -> Optional[str]:
        """Catalog id stamped on a synthetic library row, else ``None``."""
        extra = getattr(entry, "extra", None) or {}
        game_id = extra.get("library_game_id")
        return game_id or None

    @staticmethod
    def _library_identity_key(entry: SaveEntry) -> Optional[str]:
        """ROM identity_key persisted on a catalog row, else ``None``.

        Library rows carry the key recorded at backup time; a legacy row has
        none, so callers fall back to the catalog id and never fabricate a ROM
        identity.
        """
        extra = getattr(entry, "extra", None) or {}
        key = extra.get("identity_key")
        if not key:
            return None
        return str(key).strip() or None

    def _game_id(self, entry: SaveEntry) -> str:
        """Catalog/device game key for an entry, preferring the library marker."""
        return self._library_game_id(entry) or game_key(entry)

    def library_entries(self) -> List[SaveEntry]:
        """One row per catalog game (newest first), with statuses pre-computed.

        A library row is by definition already backed up, so its status is
        ``unchanged`` and no hashing (or scanning of a device) is needed.
        """
        catalog = load_catalog(self.library_root)
        entries = catalog_entries(catalog, self.library_root)
        self._backup_statuses = {}
        for entry in entries:
            game = catalog.games.get(entry.extra.get("library_game_id"))
            self._backup_statuses[entry.path] = self._library_status(game)
        return entries

    def _library_status(self, game: Optional[object]) -> SaveBackupStatus:
        latest = latest_snapshot(game) if game is not None else None
        if latest is None:
            return SaveBackupStatus(status="new")
        return SaveBackupStatus(
            status="unchanged",
            source_mtime=path_mtime_iso(latest.absolute_path(self.library_root)),
            last_backup_at=latest.created_at,
            sha256=latest.sha256,
        )

    def set_library_mode(self, enabled: bool) -> bool:
        """Switch the browse source between the device and the local library.

        Switching source clears the platform filter so a stale selection can
        never leave a newly-selected source looking empty.
        """
        enabled = bool(enabled)
        if enabled != self.library_mode:
            self.library_mode = enabled
            self.selected_platform = "all"
            self._backup_statuses = {}
        self._refresh_source_status()
        return self.library_mode

    def _refresh_source_status(self) -> None:
        if self.library_mode:
            self.status_text = f"本地存档库 · {len(self.visible_saves())} 款游戏 | [只读]"
        elif self.current_result is not None:
            self.status_text = _build_status_text(
                self.current_result, counts=self.backup_status_counts()
            )
        else:
            self.status_text = "就绪"

    def all_saves(self) -> List[SaveEntry]:
        if self.library_mode:
            return self.library_entries()
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
                if catalog.games.get(self._game_id(save))
                and catalog.games[self._game_id(save)].starred
            ]
        # Filter combination is unchanged: new + changed stay visible, only
        # unchanged rows are dropped when "仅显示有更新" is active. Every library
        # row is already backed up, so the filter is not applied in library mode
        # (otherwise it would blank the whole browser).
        if self.hide_unchanged and not self.library_mode:
            saves = [save for save in saves if self.save_status(save).status != "unchanged"]
        return saves

    def save_status(self, entry: SaveEntry) -> SaveBackupStatus:
        cached = self._backup_statuses.get(entry.path)
        if cached is not None:
            return cached
        if self._library_game_id(entry) is not None:
            game = load_catalog(self.library_root).games.get(self._library_game_id(entry))
            status = self._library_status(game)
        else:
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
        if self.library_mode:
            self.library_entries()  # populates statuses without hashing device files
            return self.backup_status_counts()
        entries = self.all_saves()
        prepared = self.prepare_backup_statuses(entries)
        self.apply_backup_statuses(prepared)
        return prepared.status_counts

    def _calculate_device_statuses(
        self, entries: List[SaveEntry]
    ) -> Tuple[Dict[str, SaveBackupStatus], List[str], Dict[str, int]]:
        """计算设备存档状态，不修改 AppState，供后台扫描安全调用。"""
        statuses: Dict[str, SaveBackupStatus] = {}
        status_warnings: List[str] = []
        catalog = load_catalog(self.library_root)
        pending: List[SaveEntry] = []
        for entry in entries:
            game = catalog.games.get(game_key(entry))
            if not game or not game.versions:
                statuses[entry.path] = classify_save_status(entry, catalog, digest=None)
            else:
                pending.append(entry)
        if pending:
            def _hash_one(item: SaveEntry) -> Tuple[str, Optional[str], Optional[BaseException]]:
                try:
                    return item.path, hash_tree(Path(item.path)), None
                except (OSError, ValueError, FileNotFoundError) as exc:
                    return item.path, None, exc

            workers = min(2, len(pending))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                hashed = list(pool.map(_hash_one, pending))
            by_path = {path: (digest, err) for path, digest, err in hashed}
            for entry in pending:
                digest, err = by_path[entry.path]
                if err is not None:
                    status_warnings.append(
                        f"计算存档哈希失败: {entry.display_name or entry.path}: {err}"
                    )
                    statuses[entry.path] = classify_save_status(
                        entry, catalog, hash_error=True
                    )
                else:
                    statuses[entry.path] = classify_save_status(
                        entry, catalog, digest=digest
                    )
        counts = {"new": 0, "changed": 0, "unchanged": 0}
        for status in statuses.values():
            counts[status.status] = counts.get(status.status, 0) + 1
        return statuses, status_warnings, counts

    def set_search_query(self, query: str) -> None:
        self.search_query = query or ""

    def toggle_starred_only(self) -> bool:
        self.starred_only = not self.starred_only
        return self.starred_only

    def toggle_star(self, entry: SaveEntry) -> bool:
        catalog = load_catalog(self.library_root)
        current = catalog.games.get(self._game_id(entry))
        starred = not bool(current and current.starred)
        game = set_game_meta(self.library_root, entry, starred=starred)
        self.status_text = "已加入收藏" if game.starred else "已取消收藏"
        return game.starred

    def is_starred(self, entry: SaveEntry) -> bool:
        game = load_catalog(self.library_root).games.get(self._game_id(entry))
        return bool(game and game.starred)

    def game_note(self, entry: SaveEntry) -> str:
        game = load_catalog(self.library_root).games.get(self._game_id(entry))
        return game.note if game else ""

    def set_note(self, entry: SaveEntry, note: str) -> None:
        set_game_meta(self.library_root, entry, note=note)
        self.status_text = "已保存备注"

    def delete_library_game(self, entry: SaveEntry) -> GameDeletion:
        """Delete one game's local snapshots and covers from the library.

        ``entry`` may be a library row or a device save; either way only the
        catalog game it maps to is removed, never the device save itself. The
        caller is responsible for confirming intent first (a cancelled delete
        must never reach this method).
        """
        game_id = self._game_id(entry)
        result = delete_game(self.library_root, game_id)
        self._backup_statuses = {}
        name = entry.display_name or game_id
        if result.ok:
            self.status_text = f"已删除备份 · {name}"
        elif not result.found:
            self.status_text = "未找到要删除的备份"
        else:
            self.status_text = (
                f"删除未完成 · {name}（保留 {result.snapshots_retained} 个版本）"
            )
        return result

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
        if self.library_mode:
            # Library browsing is a single cross-platform list ordered by the
            # most recent backup; platform grouping would scramble that order.
            return [("all", saves)]
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
        if self.library_mode:
            self.status_text = f"本地存档库 · {label} · {count} 款游戏 | [只读]"
        elif self.current_result:
            self.status_text = f"{label} · {count} 个存档 | [只读]"

    def _identity_key_for_backup(self, entry: SaveEntry) -> Optional[str]:
        """Best-effort ROM identity key to persist with a backup.

        ``None`` means the resolver could not identify the ROM (unresolved or
        ambiguous); it is forwarded as-is so ``backup_save`` never clobbers an
        identity a previous backup already stored.
        """
        try:
            return self.resolve_save_identity(entry).identity_key
        except Exception:  # noqa: BLE001 - identity is best-effort; backup proceeds
            return None

    def import_save(self, entry: SaveEntry) -> Optional[Path]:
        """Backup one save into the versioned local library. Never writes to the source volume."""
        if (
            self._library_game_id(entry) is not None
            or self.library_mode
            or is_inside_library(entry.path, self.library_root)
        ):
            # The source would be a snapshot inside the library itself; copying it
            # back in would duplicate the library into its own tree.
            self.status_text = "本地存档库无需备份"
            return None
        identity_key = self._identity_key_for_backup(entry)
        try:
            result = backup_save(entry, self.library_root, identity_key=identity_key)
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
        catalog = load_catalog(self.library_root)
        game_id = self._library_game_id(entry)
        if game_id is not None:
            game = catalog.games.get(game_id)
            return list(game.versions) if game is not None else []
        return versions_for(catalog, entry)

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
        """Fetch latest volume list from provider.

        Pulled FTP caches are not provider volumes, so they are preserved across
        a refresh (when they still exist) instead of vanishing from the device
        list.
        """
        preserved = [
            volume
            for volume in self.volumes
            if (volume.extra or {}).get("ftp") and Path(volume.mount_point).is_dir()
        ]
        try:
            self.volumes = self.provider.list_volumes()
        except Exception as e:
            self.warnings.append(f"刷新卷列表失败: {e}")
            self.volumes = []
        for volume in preserved:
            if not any(
                Path(existing.mount_point) == Path(volume.mount_point)
                for existing in self.volumes
            ):
                self.volumes.append(volume)
        return self.volumes

    def begin_mount_scan(self, mount_point: Union[Path, str], auto: bool = False) -> Path:
        """在主线程切换到待扫描设备，并清除上一设备的展示状态。"""
        path = Path(mount_point)
        if self.library_mode or self.current_mount != path:
            self.selected_platform = "all"
        self.library_mode = False
        self.current_mount = path
        self._auto_selected_mount = auto
        self._identity_resolver = None
        self.current_result = None
        self._backup_statuses = {}
        self.warnings = []
        self.status_text = f"正在扫描: {path}"
        return path

    def prepare_mount_scan(self, mount_point: Union[Path, str]) -> PreparedMountScan:
        """扫描并计算廉价备份状态（不计算哈希）；可在工作线程运行。"""
        path = Path(mount_point)
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
        catalog = load_catalog(self.library_root)
        statuses: Dict[str, SaveBackupStatus] = {}
        for entry in res.saves:
            game = catalog.games.get(game_key(entry))
            latest = game.versions[-1] if game and game.versions else None
            if latest is None:
                statuses[entry.path] = SaveBackupStatus(
                    status="new",
                    source_mtime=path_mtime_iso(entry.path),
                    last_backup_at=None,
                    mtime_stale=False,
                    sha256=None,
                )
            else:
                statuses[entry.path] = SaveBackupStatus(
                    status="changed",
                    source_mtime=path_mtime_iso(entry.path),
                    last_backup_at=latest.created_at,
                    mtime_stale=False,
                    sha256=None,
                )
        counts = {"new": 0, "changed": 0, "unchanged": 0}
        for status in statuses.values():
            counts[status.status] = counts.get(status.status, 0) + 1
        return PreparedMountScan(
            mount_point=path,
            result=res,
            backup_statuses=statuses,
            status_warnings=(),
            status_counts=counts,
            status_text=_build_status_text(res, counts=counts),
        )

    def apply_prepared_mount_scan(self, prepared: PreparedMountScan) -> ScanResult:
        """在主线程一次性提交后台扫描结果。"""
        self.current_mount = prepared.mount_point
        self.current_result = prepared.result
        self._identity_resolver = None
        self._backup_statuses = dict(prepared.backup_statuses)
        self.warnings = [*prepared.result.warnings, *prepared.status_warnings]
        self.status_text = prepared.status_text
        return prepared.result

    def prepare_backup_statuses(
        self, entries: Optional[Sequence[SaveEntry]] = None
    ) -> PreparedBackupStatuses:
        """计算设备存档真实哈希与状态；可在工作线程运行。最多 2 个工作线程。"""
        target = (
            list(entries)
            if entries is not None
            else (list(self.current_result.saves) if self.current_result else [])
        )
        statuses, status_warnings, counts = self._calculate_device_statuses(target)
        status_text = None
        if self.current_result is not None:
            status_text = _build_status_text(self.current_result, counts=counts)
        return PreparedBackupStatuses(
            statuses=statuses,
            status_warnings=tuple(status_warnings),
            status_counts=counts,
            status_text=status_text,
            entries=tuple(target),
            mount_point=self.current_mount,
        )

    def apply_backup_statuses(self, prepared: PreparedBackupStatuses) -> bool:
        """在主线程提交后台哈希结果。若当前展示的已不是该批存档则丢弃 (no-op)。"""
        if self.current_result is None:
            return False
        if prepared.mount_point is not None and self.current_mount != prepared.mount_point:
            return False
        if prepared.entries and tuple(self.current_result.saves) != prepared.entries:
            return False
        self._backup_statuses.update(prepared.statuses)
        self.warnings.extend(prepared.status_warnings)
        self.status_text = _build_status_text(
            self.current_result, counts=self.backup_status_counts()
        )
        return True

    def select_mount(self, mount_point: Union[Path, str], auto: bool = False) -> ScanResult:
        """Select a volume or path synchronously and update current result.

        ``auto`` marks a selection the app made on the user's behalf; only those
        may later be replaced by a better device (see ``ensure_mount_selected``).
        """
        path = self.begin_mount_scan(mount_point, auto=auto)
        prepared_scan = self.prepare_mount_scan(path)
        self.apply_prepared_mount_scan(prepared_scan)
        prepared_statuses = self.prepare_backup_statuses(prepared_scan.result.saves)
        self.apply_backup_statuses(prepared_statuses)
        return prepared_scan.result

    def register_custom_path(self, path: Union[Path, str]) -> Path:
        """把用户选择的目录加入设备列表，但不立即扫描。"""
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
        return custom_path

    def select_custom_path(self, path: Union[Path, str]) -> ScanResult:
        """Select an arbitrary folder from file dialog and scan it."""
        custom_path = self.register_custom_path(path)
        return self.select_mount(custom_path)

    def preferred_volume(self) -> Optional[VolumeInfo]:
        """The removable device the app should default to, else ``None``.

        Fixed disks (C:/D:/E:) are deliberately never auto-selected: a built-in
        drive is not a handheld card, and scanning it on startup is both slow and
        surprising. The user can still open one explicitly through the
        "其他设备" entry.
        """
        removable = [volume for volume in self.volumes if volume.is_removable]
        if not removable:
            return None
        return min(removable, key=_mount_sort_key)

    def mount_selection_candidate(self) -> Optional[VolumeInfo]:
        """返回当前应该自动扫描的设备，但不执行扫描。"""
        if self.library_mode:
            return None
        preferred = self.preferred_volume()
        if preferred is None:
            return None
        if self.current_mount is not None:
            if not self._auto_selected_mount:
                return None
            if Path(preferred.mount_point) == Path(self.current_mount):
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
        self.select_mount(preferred.mount_point, auto=True)
        return preferred

    def apply_watch_event(
        self, event_type: str, volume: VolumeInfo, *, auto_select: bool = True
    ) -> None:
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
            if auto_select:
                self.ensure_mount_selected()
            if (
                (not auto_select and self.current_mount != v_mount)
                or (auto_select and self.current_mount == before)
            ):
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
                if auto_select:
                    self.ensure_mount_selected()
            else:
                self.status_text = f"设备/卷已拔出: {volume.name}"

    def drain_events(self, *, auto_select: bool = True) -> int:
        """Process all queued watch events on the main thread."""
        count = 0
        while True:
            try:
                event_type, volume = self.event_queue.get_nowait()
            except queue.Empty:
                break
            self.apply_watch_event(event_type, volume, auto_select=auto_select)
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

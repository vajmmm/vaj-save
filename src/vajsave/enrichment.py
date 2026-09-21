"""Identity, metadata, artwork, and LLM helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Union

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
from .identity import (
    BINDINGS_NAME,
    ROM_CACHE_NAME,
    BindingStore,
    GameIdentity,
    GameIdentityResolver,
    GameIdentityResult,
    resolved,
)
from .metadata import (
    METADATA_CACHE_NAME,
    GameMetadata,
    GameMetadataResolver,
    LibretroMetadataProvider,
    MetadataCache,
    default_libretro_dirs,
)
from .models import SaveEntry

if TYPE_CHECKING:
    from .app_state import AppState

# Platforms whose libretro provider name is the save's own SFO / display title
# rather than a ROM-digest index canonical title.
_TITLE_PROVIDER_PLATFORMS = ("psp", "vita", "3ds")

# Sentinel distinguishing "leave this setting untouched" from an explicit None
# (which clears a persisted ROM directory) in ``set_rom_dirs``.
UNSET = object()

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
    "EDGB",
    "GBOS",
)
_CARTRIDGE_PLATFORMS = ("gba", "nds", "gb", "gbc")


class Enrichment:
    def __init__(self, app: AppState) -> None:
        self.app = app

    def set_rom_dirs(
        self,
        gba_rom_dir: object = UNSET,
        nds_rom_dir: object = UNSET,
        *,
        gb_rom_dir: object = UNSET,
        gbc_rom_dir: object = UNSET,
    ) -> Tuple[Optional[Path], Optional[Path]]:
        """Persist cartridge ROM directories used for identity.

        Positional arguments stay ``(gba_rom_dir, nds_rom_dir)``. ``gb_rom_dir``
        and ``gbc_rom_dir`` are keyword-only. Only the arguments that are passed
        are touched: the default sentinel keeps the current value, ``None``/blank
        clears it. Returns the new ``(gba_rom_dir, nds_rom_dir)`` pair.
        """
        app = self.app
        config = app.settings.load()
        if gba_rom_dir is not UNSET:
            app.gba_rom_dir = app._coerce_dir(gba_rom_dir)
            if app.gba_rom_dir is not None:
                config["gba_rom_dir"] = str(app.gba_rom_dir)
            else:
                config.pop("gba_rom_dir", None)
        if nds_rom_dir is not UNSET:
            app.nds_rom_dir = app._coerce_dir(nds_rom_dir)
            if app.nds_rom_dir is not None:
                config["nds_rom_dir"] = str(app.nds_rom_dir)
            else:
                config.pop("nds_rom_dir", None)
        if gb_rom_dir is not UNSET:
            app.gb_rom_dir = app._coerce_dir(gb_rom_dir)
            if app.gb_rom_dir is not None:
                config["gb_rom_dir"] = str(app.gb_rom_dir)
            else:
                config.pop("gb_rom_dir", None)
        if gbc_rom_dir is not UNSET:
            app.gbc_rom_dir = app._coerce_dir(gbc_rom_dir)
            if app.gbc_rom_dir is not None:
                config["gbc_rom_dir"] = str(app.gbc_rom_dir)
            else:
                config.pop("gbc_rom_dir", None)
        app.settings.save(config)
        app._identity_resolver = None
        app._metadata_service = None
        return (app.gba_rom_dir, app.nds_rom_dir)

    def _should_search_volume_for_roms(self, root: Path) -> bool:
        """True when ``root`` is a handheld card, not the OS system drive."""
        app = self.app
        try:
            resolved_root = root.resolve()
        except OSError:
            resolved_root = root
        if sys.platform == "win32":
            system = os.environ.get("SystemDrive", "C:").rstrip("\\").upper()
            drive = (resolved_root.drive or "").rstrip("\\").upper()
            if drive and drive == system:
                return False
        else:
            posix = resolved_root.as_posix()
            if posix in ("/", "/System", "/Applications"):
                return False
        for name in _HANDHELD_ROM_MARKERS:
            try:
                if (resolved_root / name).is_dir():
                    return True
            except OSError:
                continue
        for rel in ("roms/gb", "roms/gbc"):
            try:
                if (resolved_root.joinpath(*rel.split("/"))).is_dir():
                    return True
            except OSError:
                continue
        if app.current_result is not None:
            if app.current_result.platform in _CARTRIDGE_PLATFORMS:
                return True
            if any(s.platform in _CARTRIDGE_PLATFORMS for s in app.current_result.sources):
                return True
            if any(s.platform in _CARTRIDGE_PLATFORMS for s in app.current_result.saves):
                return True
        return False

    def _volume_rom_search_roots(self) -> List[Path]:
        app = self.app
        if app.current_mount is None:
            return []
        try:
            root = Path(app.current_mount).expanduser()
            if not root.is_dir():
                return []
        except OSError:
            return []
        if not self._should_search_volume_for_roms(root):
            return []
        return [root]

    def _build_identity_resolver(self) -> GameIdentityResolver:
        app = self.app
        extra = self._volume_rom_search_roots()
        rom_dirs: Dict[str, List[Path]] = {}
        gba_roots = ([app.gba_rom_dir] if app.gba_rom_dir is not None else []) + extra
        nds_roots = ([app.nds_rom_dir] if app.nds_rom_dir is not None else []) + extra
        gb_roots = ([app.gb_rom_dir] if app.gb_rom_dir is not None else []) + extra
        gbc_roots = ([app.gbc_rom_dir] if app.gbc_rom_dir is not None else []) + extra
        if gba_roots:
            rom_dirs["gba"] = gba_roots
        if nds_roots:
            rom_dirs["nds"] = nds_roots
        if gb_roots:
            rom_dirs["gb"] = gb_roots
        if gbc_roots:
            rom_dirs["gbc"] = gbc_roots
        bindings = BindingStore(app.library_root / BINDINGS_NAME)
        return GameIdentityResolver(
            rom_dirs=rom_dirs,
            bindings=bindings,
            cache_path=app.library_root / ROM_CACHE_NAME,
        )

    @property
    def identity_resolver(self) -> GameIdentityResolver:
        app = self.app
        if app._identity_resolver is None:
            app._identity_resolver = self._build_identity_resolver()
        return app._identity_resolver

    def _libretro_dirs(self) -> List[Path]:
        app = self.app
        return default_libretro_dirs(
            app.library_root, configured=app.settings.load().get("libretro_dir")
        )

    @property
    def metadata_resolver(self) -> GameMetadataResolver:
        """Cache-first metadata resolver backed by the local libretro index."""
        app = self.app
        if app._metadata_service is None:
            provider = LibretroMetadataProvider(self._libretro_dirs())
            cache = MetadataCache(app.library_root / METADATA_CACHE_NAME)
            app._metadata_service = GameMetadataResolver(provider, cache)
        return app._metadata_service

    @property
    def artwork_service(self) -> ArtworkService:
        """Cover provider/downloader bound to the library's ``covers/`` tree."""
        app = self.app
        if app._artwork_service is None:
            cache = CoverCache(app.library_root / COVER_CACHE_DIR)
            app._artwork_service = ArtworkService(
                cache=cache, llm_chooser=self._build_llm_chooser()
            )
        return app._artwork_service

    def _build_llm_chooser(self):
        """The optional cover chooser, or ``None`` when disabled/unconfigured."""
        app = self.app
        if not app.llm_cover_enabled or not app.llm_api_key:
            return None
        return LLMCoverChooser(
            api_key=app.llm_api_key,
            model=app.llm_model,
            base_url=app.llm_base_url,
            protocol=app.llm_protocol,
            log_path=self.llm_log_path(),
        )

    def llm_log_path(self) -> Path:
        """Where the optional LLM request/result debug log is written."""
        return Path(self.app.library_root) / LLM_LOG_NAME

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
        app = self.app
        key = app.llm_api_key if api_key is None else str(api_key).strip()
        if not key:
            return False, "请先填写 API 密钥"
        chooser = LLMCoverChooser(
            api_key=key,
            model=app.llm_model if model is None else model,
            base_url=app.llm_base_url if base_url is None else base_url,
            protocol=app.llm_protocol if protocol is None else protocol,
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
        api_key: object = UNSET,
        base_url: object = UNSET,
        model: object = UNSET,
        protocol: object = UNSET,
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
        app = self.app
        if protocol is not UNSET:
            new_protocol = normalize_protocol(protocol)
            if new_protocol != app.llm_protocol:
                current_base = app.llm_base_url if base_url is UNSET else base_url
                current_model = app.llm_model if model is UNSET else model
                base_url = self._follow_protocol_default(
                    current_base,
                    default_base_url(app.llm_protocol),
                    default_base_url(new_protocol),
                )
                model = self._follow_protocol_default(
                    current_model,
                    default_model(app.llm_protocol),
                    default_model(new_protocol),
                )
                app.llm_protocol = new_protocol
        if enabled is not None:
            app.llm_cover_enabled = bool(enabled)
        if api_key is not UNSET:
            app.llm_api_key = app._coerce_text(api_key)
        if base_url is not UNSET:
            app.llm_base_url = normalize_base_url(base_url, app.llm_protocol)
        if model is not UNSET:
            app.llm_model = app._coerce_text(model) or default_model(app.llm_protocol)
        config = app.settings.load()
        config["llm_cover_enabled"] = app.llm_cover_enabled
        if app.llm_api_key:
            config["llm_api_key"] = app.llm_api_key
        else:
            config.pop("llm_api_key", None)
        config["llm_protocol"] = app.llm_protocol
        config["llm_base_url"] = app.llm_base_url
        config["llm_model"] = app.llm_model
        app.settings.save(config)
        app._artwork_service = None

    def set_libretro_dir(self, value: object = None) -> Optional[Path]:
        """Persist (or clear) the optional libretro ``.dat`` directory."""
        app = self.app
        config = app.settings.load()
        resolved_dir = app._coerce_dir(value)
        if resolved_dir is not None:
            config["libretro_dir"] = str(resolved_dir)
        else:
            config.pop("libretro_dir", None)
        app.settings.save(config)
        app.libretro_dir = resolved_dir
        app._metadata_service = None
        return resolved_dir

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
        app = self.app
        try:
            if identity is None:
                identity = result.identity if result else None
            if identity is None:
                identity = self.resolve_save_identity(entry).identity
            key = identity.identity_key if identity is not None else None
            return self.artwork_service.resolve(
                entry,
                app.library_root,
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
        app = self.app
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
                        library_root=app.library_root,
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
                library_root=app.library_root,
            )
        except Exception:  # noqa: BLE001
            return PLACEHOLDER

    def resolve_save_identity(self, entry: SaveEntry) -> GameIdentityResult:
        app = self.app
        game_id = app._library_game_id(entry)
        if game_id is not None:
            # A catalog row already knows which game it is; never re-resolve it
            # against a device or trigger a ROM lookup. The persisted ROM
            # identity_key (when present) is what the cover cache is keyed by;
            # without one we fall back to the catalog id, which misses the
            # identity-hash cache and keeps the placeholder.
            identity = GameIdentity(
                identity_key=app._library_identity_key(entry) or game_id,
                platform=getattr(entry, "platform", "") or "unknown",
                title=entry.display_name or game_id,
                title_id=entry.title_id or None,
            )
            return resolved(identity, reason="library", save_path=entry.path)
        return self.identity_resolver.resolve(entry)

    def resolve_identities(
        self, entries: Optional[List[SaveEntry]] = None
    ) -> List[GameIdentityResult]:
        app = self.app
        target = list(entries) if entries is not None else app.all_saves()
        if app.library_mode:
            return [self.resolve_save_identity(entry) for entry in target]
        return self.identity_resolver.resolve_many(target)

    def bind_save_identity(
        self,
        entry: SaveEntry,
        identity: Optional[GameIdentity] = None,
        rom_path: Optional[Union[Path, str]] = None,
    ) -> GameIdentity:
        return self.identity_resolver.bind(entry, identity=identity, rom_path=rom_path)

"""FTP presets, configuration, and cache pull."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Union

from .ftp_fetch import FtpPullResult, cache_dir_for, ftp_cache_root, pull_preset
from .models import VolumeInfo
from .remote_ftp import (
    DEFAULT_PRESET_KEY,
    FtpProfile,
    get_preset,
    preset_keys,
    presets,
)

if TYPE_CHECKING:
    from .app_state import AppState


def parse_port(value: object) -> Optional[int]:
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


def resolve_ftp_preset_key(config: dict) -> str:
    configured = "" if config.get("ftp_preset") is None else str(config.get("ftp_preset")).strip().lower()
    return configured if configured in preset_keys() else DEFAULT_PRESET_KEY


class FtpSession:
    def __init__(self, app: AppState) -> None:
        self.app = app

    def ftp_presets(self) -> List[FtpProfile]:
        """Built-in presets with the persisted host/port/user overrides applied."""
        return [self._ftp_profile(preset) for preset in presets()]

    def _ftp_profile(self, preset: FtpProfile) -> FtpProfile:
        app = self.app
        overrides = {}
        if app.ftp_host:
            overrides["host"] = app.ftp_host
        if app.ftp_port:
            overrides["port"] = app.ftp_port
        if app.ftp_user:
            overrides["user"] = app.ftp_user
        if app._ftp_password:
            overrides["password"] = app._ftp_password
        return replace(preset, **overrides) if overrides else preset

    def current_ftp_preset(self) -> FtpProfile:
        """The active preset (Checkpoint by default)."""
        return self._ftp_profile(get_preset(self.app.ftp_preset_key))

    def ftp_cache_dir(self, preset_key: Optional[str] = None) -> Path:
        """Local cache directory that a preset is pulled into."""
        app = self.app
        return cache_dir_for(app.library_root, preset_key or app.ftp_preset_key)

    def set_ftp_preset(self, key: object) -> Optional[str]:
        """Persist the active FTP preset; unknown keys are rejected with ``None``."""
        app = self.app
        text = str(key or "").strip().lower()
        if text not in preset_keys():
            return None
        app.ftp_preset_key = text
        app.settings.update(ftp_preset=text)
        app.status_text = f"FTP 预设已切换: {get_preset(text).label}"
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
        app = self.app
        if preset_key is not None:
            self.set_ftp_preset(preset_key)
        if host is not None:
            app.ftp_host = str(host).strip()
        if port is not None:
            parsed = parse_port(port)
            if parsed is not None:
                app.ftp_port = parsed
        if user is not None:
            app.ftp_user = str(user).strip()
        if password is not None:
            app._ftp_password = str(password)
        config = app.settings.load()
        config["ftp_host"] = app.ftp_host
        config["ftp_port"] = app.ftp_port
        config["ftp_user"] = app.ftp_user
        app.settings.save(config)
        return self.current_ftp_preset()

    def _register_ftp_volume(self, profile: FtpProfile, path: Union[Path, str]) -> VolumeInfo:
        """Expose a pulled cache directory as a non-removable device row."""
        app = self.app
        mount = Path(path)
        volume = VolumeInfo(
            name=f"FTP · {profile.label}",
            mount_point=mount,
            is_removable=False,
            extra={"ftp": True, "ftp_preset": profile.key},
        )
        for index, existing in enumerate(app.volumes):
            if Path(existing.mount_point) == mount:
                app.volumes[index] = volume
                return volume
        app.volumes.append(volume)
        return volume

    def pull_ftp_saves(self) -> FtpPullResult:
        """Pull the active preset into its cache and scan it as a device.

        A failed pull is reported as a warning and leaves the current device
        selection alone, so a half-populated cache is never shown as a device.
        """
        app = self.app
        profile = self.current_ftp_preset()
        result = pull_preset(
            profile,
            ftp_cache_root(app.library_root),
            client_factory=app._ftp_client_factory,
        )
        if not result.ok or result.path is None:
            message = result.error or "未知错误"
            app.warnings.append(f"FTP 拉取失败: {message}")
            app.status_text = f"FTP 拉取失败 · {message}"
            return result
        self._register_ftp_volume(profile, result.path)
        app.select_mount(result.path)
        app.status_text = (
            f"FTP 已拉取 · {profile.label} · {result.files} 个文件 | [只读]"
        )
        return result

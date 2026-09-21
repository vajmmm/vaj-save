"""Suggest restore destinations without writing to a handheld."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .device_registry import device_key_for
from .models import SaveEntry

if TYPE_CHECKING:
    from .app_state import AppState

PLATFORM_RESTORE_RELATIVE: dict[str, tuple[str, ...]] = {
    "psp": ("PSP/SAVEDATA",),
    "vita": ("user/00/savedata", "ux0/user/00/savedata"),
    "switch": ("switch/Checkpoint/saves", "JKSV"),
    "3ds": ("3ds/Checkpoint/saves", "JKSV/Saves"),
    "gba": ("SAVER", "GBASYS/SAVE"),
    "nds": ("roms/nds/saves",),
    "gb": ("roms/gb/saves",),
    "gbc": ("roms/gbc/saves",),
}


def existing_dir(path: Optional[Path]) -> Optional[Path]:
    if path is None:
        return None
    candidate = Path(path)
    try:
        if candidate.is_dir():
            return candidate
    except OSError:
        return None
    return None


def bound_restore_dir(app: AppState, entry: SaveEntry, mount: Path) -> Optional[Path]:
    extra = app.scans._volume_extra(mount) or {}
    key = device_key_for(mount, extra)
    if not key:
        return None
    for source in app.device_registry.usable_sources(key, mount):
        if source.platform == entry.platform and source.source_id == entry.source_id:
            return existing_dir(mount / source.relative_root)
    return None


def platform_restore_dir(platform: str, mount: Path) -> Optional[Path]:
    for relative in PLATFORM_RESTORE_RELATIVE.get((platform or "").strip().lower(), ()):
        found = existing_dir(mount / relative)
        if found is not None:
            return found
    return None


def suggested_restore_dir(app: AppState, entry: SaveEntry) -> Optional[Path]:
    """Bound source, then platform defaults on the current mount, then last used."""
    mount = app.current_mount
    if mount is not None:
        mount_path = Path(mount)
        if not app.library_mode:
            bound = bound_restore_dir(app, entry, mount_path)
            if bound is not None:
                return bound
        platform_dir = platform_restore_dir(entry.platform, mount_path)
        if platform_dir is not None:
            return platform_dir
    return existing_dir(app.last_restore_dir)


def remember_restore_dir(app: AppState, destination: Path) -> Path:
    path = Path(destination)
    app.last_restore_dir = path
    app.settings.update(last_restore_dir=str(path))
    return path

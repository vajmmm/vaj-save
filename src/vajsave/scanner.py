"""Public save-scan API: validate root, dispatch platform scanners, merge results."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Set, Union

from .models import SaveEntry, SaveSource, ScanResult
from .covers import find_embedded_cover
from .platforms import gba, nds, psp, switch, threeds, vita
from .platforms.common import (
    collect_unique_dirs,
    find_pattern_dirs,
    is_safe_path,
    resolved_key,
    safe_iterdir,
)

# Back-compat aliases for tests and external callers that import private helpers.
_MAX_WRAPPER_DEPTH = 2
_is_safe_path = is_safe_path
_safe_iterdir = safe_iterdir
_resolved_key = resolved_key
_find_pattern_dirs = find_pattern_dirs
_collect_unique_dirs = collect_unique_dirs


def _path_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def guess_platform(root: Union[Path, str]) -> Optional[str]:
    """Shallowly guess the handheld platform of a volume root.

    Only the root and a fixed set of known relative paths are probed with
    bounded ``is_dir``/``exists`` checks. It never recurses or walks the
    filesystem, so it is safe to call against large or partially-unreadable
    volumes. Returns ``None`` when the platform cannot be determined.
    """
    try:
        base = Path(root)
        if not base.is_dir():
            return None
    except Exception:
        return None

    def has_dir(rel: str) -> bool:
        return _path_is_dir(base / rel)

    def has_file(rel: str) -> bool:
        return _path_exists(base / rel)

    # PS Vita: native savedata or exported savegames (ux0/ is the Vita mount).
    if (
        has_dir("user/00/savedata")
        or has_dir("ux0/user/00/savedata")
        or has_dir("data/savegames")
        or has_dir("ux0/data/savegames")
    ):
        return "vita"

    # PSP: SAVEDATA container, also nested inside the Vita pspemu tree.
    if (
        has_dir("PSP/SAVEDATA")
        or has_dir("pspemu/PSP/SAVEDATA")
        or has_dir("ux0/pspemu/PSP/SAVEDATA")
    ):
        return "psp"
    if base.name.upper() == "SAVEDATA":
        return "psp"

    # Checkpoint exports.
    if has_dir("switch/Checkpoint/saves"):
        return "switch"
    if has_dir("3ds/Checkpoint/saves"):
        return "3ds"

    # JKSV is shared: Switch uses JKSV/ directly, 3DS JKSM nests Saves/ExtData/SysSave.
    if has_dir("JKSV"):
        if any(has_dir(f"JKSV/{cat}") for cat in ("Saves", "ExtData", "SysSave")):
            return "3ds"
        return "switch"

    if has_dir("Nintendo 3DS"):
        return "3ds"
    if has_dir("switch") or has_dir("atmosphere"):
        return "switch"

    # GBA flash carts.
    if (
        has_dir("SAVER")
        or has_dir("GBASYS/SAVE")
        or has_dir("EDGBA/gamedata")
        or has_dir(".superfw")
    ):
        return "gba"

    # NDS flash carts / TWiLight Menu++.
    if (
        has_dir("roms/nds")
        or has_dir("_nds")
        or has_dir("TTMenu")
        or has_file("R4.dat")
        or has_file("_system_")
    ):
        return "nds"

    return None


def scan(root_path: Union[Path, str]) -> ScanResult:
    """Scan a root directory (e.g. mounted USB volume or SD card) and list saves."""
    root = Path(root_path)
    warnings: List[str] = []

    try:
        if not root.exists() or not root.is_dir():
            warnings.append(f"Target path does not exist or is not a directory: {root}")
            return ScanResult(
                root_path=str(root),
                platform="unknown",
                sources=[],
                saves=[],
                warnings=warnings,
            )
        root_resolved = root.resolve()
    except Exception as e:
        warnings.append(f"Failed to access root path {root}: {e}")
        return ScanResult(
            root_path=str(root),
            platform="unknown",
            sources=[],
            saves=[],
            warnings=warnings,
        )

    sources: List[SaveSource] = []
    saves: List[SaveEntry] = []
    seen_source_roots: Set[Path] = set()
    seen_save_paths: Set[Path] = set()

    scanners = (
        psp.scan_psp,
        vita.scan_vita,
        switch.scan_switch,
        threeds.scan_threeds,
        gba.scan_gba,
        nds.scan_nds,
    )
    for scan_fn in scanners:
        scan_fn(
            root,
            root_resolved,
            warnings,
            sources,
            saves,
            seen_source_roots,
            seen_save_paths,
        )

    if not sources:
        platform = "unknown"
    else:
        unique_platforms = list(dict.fromkeys(s.platform for s in sources))
        platform = unique_platforms[0]

    # Fill embedded cover icons during the scan: a bounded, read-only lookup of
    # well-known icon names inside each save folder (never a disk-wide walk).
    for entry in saves:
        if entry.cover_path:
            continue
        cover = find_embedded_cover(entry.path, max_depth=1)
        if cover is not None:
            entry.cover_path = str(cover)

    return ScanResult(
        root_path=str(root),
        platform=platform,
        sources=sources,
        saves=saves,
        warnings=warnings,
    )

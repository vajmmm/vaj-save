"""Public save-scan API: validate root, dispatch platform scanners, merge results."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional, Set, Union

from .models import SaveEntry, SaveSource, ScanResult
from .covers import find_embedded_cover
from .platforms import gba, nds, psp, switch, threeds, vita
from .platforms.common import (
    ScanProgress,
    collect_unique_dirs,
    emit_scan_progress,
    find_pattern_dirs,
    is_safe_path,
    resolved_key,
    scan_cache,
    scan_progress_callback,
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


def _detected_platforms(base: Path) -> Set[str]:
    """Return platforms advertised by fixed, non-recursive paths at a device root.

    An empty set means the path may be a wrapped backup or a user-selected
    subdirectory, so callers must retain the compatible full scan.
    """
    detected: Set[str] = set()

    def has_dir(rel: str) -> bool:
        return _path_is_dir(base / rel)

    def has_file(rel: str) -> bool:
        return _path_exists(base / rel)

    if any(
        has_dir(rel)
        for rel in (
            "user/00/savedata",
            "ux0/user/00/savedata",
            "data/savegames",
            "ux0/data/savegames",
        )
    ):
        detected.add("vita")

    if (
        base.name.upper() == "SAVEDATA"
        or any(
            has_dir(rel)
            for rel in (
                "PSP/SAVEDATA",
                "pspemu/PSP/SAVEDATA",
                "ux0/pspemu/PSP/SAVEDATA",
            )
        )
    ):
        detected.add("psp")

    if has_dir("switch/Checkpoint/saves") or has_dir("switch") or has_dir("atmosphere"):
        detected.add("switch")
    if has_dir("3ds/Checkpoint/saves") or has_dir("Nintendo 3DS"):
        detected.add("3ds")

    if has_dir("JKSV"):
        # A JKSV root may contain Switch games and the reserved 3DS JKSM
        # categories at the same time, so keep both scanners when applicable.
        detected.add("switch")
        if any(has_dir(f"JKSV/{cat}") for cat in ("Saves", "ExtData", "SysSave")):
            detected.add("3ds")

    if any(
        has_dir(rel)
        for rel in ("SAVER", "GBASYS/SAVE", "EDGBA/gamedata", ".superfw")
    ):
        detected.add("gba")

    if (
        any(has_dir(rel) for rel in ("roms/nds", "_nds", "__rpg", "TTMenu"))
        or has_file("R4.dat")
        or has_file("_system_")
    ):
        detected.add("nds")

    return detected


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

    # NDS flash carts / TWiLight Menu++ / Wood R4 (__rpg).
    if (
        has_dir("roms/nds")
        or has_dir("_nds")
        or has_dir("__rpg")
        or has_dir("TTMenu")
        or has_file("R4.dat")
        or has_file("_system_")
    ):
        return "nds"

    return None


PLATFORM_PROGRESS_LABELS = {
    "psp": "PSP",
    "vita": "PS Vita",
    "switch": "Switch",
    "3ds": "3DS",
    "gba": "GBA",
    "nds": "NDS",
}


def scan(
    root_path: Union[Path, str],
    progress: Optional[Callable[[ScanProgress], None]] = None,
) -> ScanResult:
    """Scan a root directory (e.g. mounted USB volume or SD card) and list saves."""
    with scan_progress_callback(progress):
        return _scan_root(root_path)


def _scan_root(root_path: Union[Path, str]) -> ScanResult:
    root = Path(root_path)
    warnings: List[str] = []

    try:
        # ``is_dir`` already reports false for a missing path. Avoiding a separate
        # ``exists`` call saves a full metadata round-trip on network-mounted cards.
        if not root.is_dir():
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

    all_scanners: tuple[tuple[str, Callable[..., None]], ...] = (
        ("psp", psp.scan_psp),
        ("vita", vita.scan_vita),
        ("switch", switch.scan_switch),
        ("3ds", threeds.scan_threeds),
        ("gba", gba.scan_gba),
        ("nds", nds.scan_nds),
    )
    detected = _detected_platforms(root)
    # Recognised device roots use only scanners backed by an explicit shallow
    # fingerprint. Unknown paths retain the broad wrapper-compatible behaviour.
    scanners = [item for item in all_scanners if not detected or item[0] in detected]
    with scan_cache():
        total = len(scanners)
        for index, (platform_id, scan_fn) in enumerate(scanners, start=1):
            label = PLATFORM_PROGRESS_LABELS.get(platform_id, platform_id)
            emit_scan_progress(
                f"正在扫描 {label}… 已发现 {len(saves)} 个存档",
                index - 1,
                total,
            )
            args = (
                root,
                root_resolved,
                warnings,
                sources,
                saves,
                seen_source_roots,
                seen_save_paths,
            )
            scan_fn(*args, standard_root=bool(detected))
            emit_scan_progress(
                f"正在扫描 {label}… 已发现 {len(saves)} 个存档",
                index,
                total,
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
        if entry.platform == "vita":
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

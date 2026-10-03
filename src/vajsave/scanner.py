"""Public save-scan API: validate root, dispatch platform scanners, merge results."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Set, Union

logger = logging.getLogger("vajsave.scanner")

from .device_registry import BoundSource
from .homebrew import filter_homebrew_saves
from .models import SaveEntry, SaveSource, ScanResult
from .covers import find_embedded_cover
from .platforms import gb, gba, gbc, nds, psp, ps3, ps4, switch, threeds, vita, wii, wiiu, x360
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
    # DBI MTP Saves cache: Installed/Uninstalled games at the device root.
    if any(has_dir(rel) for rel in ("Installed games", "Uninstalled games")):
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

    if any(has_dir(rel) for rel in ("roms/gb", "EDGB", "GBOS")):
        detected.add("gb")
    if any(has_dir(rel) for rel in ("roms/gbc", "EDGB", "GBOS")):
        detected.add("gbc")

    if (
        has_dir("PS3/SAVEDATA")
        or has_dir("dev_hdd0/home")
        or has_dir("dev_usb000/PS3/SAVEDATA")
        or has_dir("dev_usb001/PS3/SAVEDATA")
    ):
        detected.add("ps3")

    if (
        has_dir("PS4/SAVEDATA")
        or has_dir("user/home")
        or has_dir("data/apollo")
    ):
        detected.add("ps4")

    if (
        has_dir("storage_mlc/usr/save/00050000")
        or has_dir("storage_usb/usr/save/00050000")
        or has_dir("usr/save/00050000")
        or has_dir("wiiu/backups")
        or has_dir("wiiu/saves")
    ):
        detected.add("wiiu")

    if (
        has_dir("savegames")
        or has_dir("wiisaves")
        or has_dir("title/00010000")
    ):
        detected.add("wii")

    if (
        has_dir("Content")
        or has_dir("Hdd1/Content")
        or has_dir("Usb0/Content")
    ):
        detected.add("x360")

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

    # DBI MTP Saves cache: games live under Installed/Uninstalled games.
    if has_dir("Installed games") or has_dir("Uninstalled games"):
        return "switch"

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

    # Game Boy / Game Boy Color (EverDrive GB, roms/gb, roms/gbc).
    if has_dir("roms/gb") or has_dir("EDGB") or has_dir("GBOS"):
        return "gb"
    if has_dir("roms/gbc"):
        return "gbc"

    # PlayStation 3
    if (
        has_dir("PS3/SAVEDATA")
        or has_dir("dev_hdd0/home")
        or has_dir("dev_usb000/PS3/SAVEDATA")
        or has_dir("dev_usb001/PS3/SAVEDATA")
    ):
        return "ps3"

    # PlayStation 4
    if (
        has_dir("PS4/SAVEDATA")
        or has_dir("user/home")
        or has_dir("data/apollo")
    ):
        return "ps4"

    # Nintendo Wii U
    if (
        has_dir("storage_mlc/usr/save/00050000")
        or has_dir("storage_usb/usr/save/00050000")
        or has_dir("usr/save/00050000")
        or has_dir("wiiu/backups")
        or has_dir("wiiu/saves")
    ):
        return "wiiu"

    # Nintendo Wii
    if (
        has_dir("savegames")
        or has_dir("wiisaves")
        or has_dir("title/00010000")
    ):
        return "wii"

    # Microsoft Xbox 360
    if (
        has_dir("Content")
        or has_dir("Hdd1/Content")
        or has_dir("Usb0/Content")
    ):
        return "x360"

    return None


PLATFORM_PROGRESS_LABELS = {
    "psp": "PSP",
    "vita": "PS Vita",
    "switch": "Switch",
    "3ds": "3DS",
    "nds": "NDS",
    "gb": "GB",
    "gbc": "GBC",
    "gba": "GBA",
    "ps3": "PS3",
    "ps4": "PS4",
    "wiiu": "Wii U",
    "wii": "Wii",
    "x360": "Xbox 360",
}


def _bound_dir(root: Path, relative_root: str) -> Optional[Path]:
    rel = (relative_root or "").strip().replace("\\", "/")
    if not rel or rel == ".":
        return root
    parts = Path(rel).parts
    if not parts or ".." in parts:
        return None
    return root.joinpath(*parts)


def _run_bound_scanner(
    scan_fn: Callable[..., None],
    root: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
    bound_items: Sequence[BoundSource],
) -> None:
    """Run one platform scanner only against the bound relative directories."""
    for item in bound_items:
        bound_dir = _bound_dir(root, item.relative_root)
        if bound_dir is None or not _path_is_dir(bound_dir):
            continue
        scan_fn(
            bound_dir,
            root_resolved,
            warnings,
            sources,
            saves,
            seen_source_roots,
            seen_save_paths,
            standard_root=True,
        )


def scan(
    root_path: Union[Path, str],
    progress: Optional[Callable[[ScanProgress], None]] = None,
    *,
    bound_sources: Sequence[BoundSource] | None = None,
) -> ScanResult:
    """Scan a root directory (e.g. mounted USB volume or SD card) and list saves.

    ``bound_sources=None`` preserves the full-scan behaviour. A non-empty list
    runs only the matching platform scanners against those relative directories
    so the rest of the volume is not walked.
    """
    with scan_progress_callback(progress):
        return _scan_root(root_path, bound_sources=bound_sources)


def _scan_root(
    root_path: Union[Path, str],
    bound_sources: Sequence[BoundSource] | None = None,
) -> ScanResult:
    root = Path(root_path)
    warnings: List[str] = []
    logger.info("开始扫描设备目录: %s", root)

    try:
        # ``is_dir`` already reports false for a missing path. Avoiding a separate
        # ``exists`` call saves a full metadata round-trip on network-mounted cards.
        if not root.is_dir():
            msg = f"Target path does not exist or is not a directory: {root}"
            logger.warning(msg)
            warnings.append(msg)
            return ScanResult(
                root_path=str(root),
                platform="unknown",
                sources=[],
                saves=[],
                warnings=warnings,
            )
        root_resolved = root.resolve()
    except Exception as e:
        msg = f"Failed to access root path {root}: {e}"
        logger.warning(msg)
        warnings.append(msg)
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
        ("nds", nds.scan_nds),
        ("gb", gb.scan_gb),
        ("gbc", gbc.scan_gbc),
        ("gba", gba.scan_gba),
        ("ps3", ps3.scan_ps3),
        ("ps4", ps4.scan_ps4),
        ("wiiu", wiiu.scan_wiiu),
        ("wii", wii.scan_wii),
        ("x360", x360.scan_x360),
    )
    bound_list = [item for item in (bound_sources or ()) if item is not None]
    if bound_list:
        allowed = {item.platform for item in bound_list}
        scanners = [item for item in all_scanners if item[0] in allowed]
        standard_root = True
        logger.info("使用已绑定的来源路径进行扫描 (平台: %s)", ", ".join(sorted(allowed)))
    else:
        detected = _detected_platforms(root)
        # Recognised device roots use only scanners backed by an explicit shallow
        # fingerprint. Unknown paths retain the broad wrapper-compatible behaviour.
        scanners = [item for item in all_scanners if not detected or item[0] in detected]
        standard_root = bool(detected)
        if detected:
            logger.info("设备特征识别命中平台: %s", ", ".join(sorted(detected)))
        else:
            logger.info("未匹配到特定平台目录特征，执行全平台兼容器扫描")
    with scan_cache():
        total = len(scanners)
        for index, (platform_id, scan_fn) in enumerate(scanners, start=1):
            label = PLATFORM_PROGRESS_LABELS.get(platform_id, platform_id)
            logger.info("正在执行平台扫描器 [%s] (%d/%d)...", label, index, total)
            emit_scan_progress(
                f"正在扫描 {label}… 已发现 {len(saves)} 个存档",
                index - 1,
                total,
            )
            if bound_list:
                _run_bound_scanner(
                    scan_fn,
                    root,
                    root_resolved,
                    warnings,
                    sources,
                    saves,
                    seen_source_roots,
                    seen_save_paths,
                    [item for item in bound_list if item.platform == platform_id],
                )
            else:
                scan_fn(
                    root,
                    root_resolved,
                    warnings,
                    sources,
                    saves,
                    seen_source_roots,
                    seen_save_paths,
                    standard_root=standard_root,
                )
            logger.info("平台扫描器 [%s] 扫描完成，累计发现 %d 个存档", label, len(saves))
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

    # Filter out homebrew utilities, system tools, and non-game apps:
    # homebrew software does not have actual game saves and is excluded from save management.
    saves = filter_homebrew_saves(saves)

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
            logger.info("发现存档内置封面: [%s] '%s' -> %s", entry.platform, entry.display_name or entry.path, cover)

    logger.info("扫描完成: %s (主平台: %s, 识别存档数: %d, 来源数: %d)", root, platform, len(saves), len(sources))
    return ScanResult(
        root_path=str(root),
        platform=platform,
        sources=sources,
        saves=saves,
        warnings=warnings,
    )

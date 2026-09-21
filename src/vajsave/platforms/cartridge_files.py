"""Shared cartridge ROM/save pairing for GB, GBC, and the GBA SAVER split."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Set, Tuple

from ..models import SaveEntry, SaveSource
from ..rom_formats import supported_extensions
from .common import (
    collect_unique_dirs,
    find_pattern_dirs,
    is_safe_path,
    resolved_key,
    safe_iterdir,
)

SAVE_EXTENSIONS = frozenset({".sav", ".srm"})
SAVER_ROM_EXTENSIONS = frozenset({".gb", ".gbc", ".gba", ".agb"})

# Root is depth 0; a ROM/save pair on the 4th nested directory is still
# reachable, matching the NDS sibling walk.
MAX_SIBLING_DEPTH = 4

_SKIP_DIR_NAMES = frozenset(
    {
        "nintendo",
        "nintendo 3ds",
        "atmosphere",
        "app",
        "appmeta",
        "pspemu",
        "user",
        "addcont",
        "patch",
        "album",
        "$recycle.bin",
        "system volume information",
    }
)


def collect_rom_stems(
    root: Path,
    root_resolved: Path,
    warnings: List[str],
    extensions: Iterable[str],
    max_depth: int = MAX_SIBLING_DEPTH,
) -> Dict[str, List[Path]]:
    """Index ROM stems under ``root`` with a bounded, cycle-safe walk."""
    wanted = frozenset(ext.lower() for ext in extensions)
    stems: Dict[str, List[Path]] = {}
    visited: Set[Path] = set()

    def walk(directory: Path, depth: int) -> None:
        key = resolved_key(directory)
        if key is None or key in visited:
            return
        visited.add(key)
        for child in safe_iterdir(directory, warnings):
            if not is_safe_path(child, root_resolved):
                continue
            if child.is_file():
                if child.suffix.lower() in wanted:
                    stems.setdefault(child.stem.casefold(), []).append(child)
            elif child.is_dir() and depth < max_depth:
                if child.name.casefold() not in _SKIP_DIR_NAMES:
                    walk(child, depth + 1)

    walk(root, 0)
    return stems


def classify_saver_platform(
    stem: str, rom_stems: Mapping[str, Sequence[Path]]
) -> str:
    """Classify an EZ-Flash ``SAVER`` file from a same-stem ROM on the volume."""
    paths = rom_stems.get(stem.casefold(), ())
    exts = {path.suffix.lower() for path in paths}
    if ".gb" in exts:
        return "gb"
    if ".gbc" in exts:
        return "gbc"
    return "gba"


def is_roms_platform_path(path: Path, platform: str) -> bool:
    """True if ``path`` is (or is under) a ``roms/<platform>`` folder pair."""
    parts_lower = [part.lower() for part in path.parts]
    plat = platform.lower()
    for index in range(len(parts_lower) - 1):
        if parts_lower[index] == "roms" and parts_lower[index + 1] == plat:
            return True
    return path.name.lower() == plat and path.parent.name.lower() == "roms"


def card_has_dirs(
    root: Path, root_resolved: Path, names: Sequence[str]
) -> bool:
    for name in names:
        marker = root / name
        try:
            if marker.exists() and is_safe_path(marker, root_resolved):
                return True
        except OSError:
            continue
    return False


def has_rom(
    directory: Path,
    extensions: Iterable[str],
    root_resolved: Path,
    warnings: List[str],
) -> bool:
    wanted = frozenset(ext.lower() for ext in extensions)
    for child in safe_iterdir(directory, warnings):
        if (
            child.is_file()
            and child.suffix.lower() in wanted
            and is_safe_path(child, root_resolved)
        ):
            return True
    return False


def ensure_source(
    *,
    source_id: str,
    platform: str,
    description: str,
    source_root: Path,
    sources: List[SaveSource],
    seen_source_roots: Set[Path],
) -> None:
    key = resolved_key(source_root)
    if key is None or key in seen_source_roots:
        return
    seen_source_roots.add(key)
    sources.append(
        SaveSource(
            source_id=source_id,
            platform=platform,
            description=description,
            root_path=str(source_root),
        )
    )


def add_save(
    sav: Path,
    *,
    platform: str,
    source_id: str,
    display_name: str,
    source_root: Path,
    description: str,
    root_resolved: Path,
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    if not sav.is_file() or not is_safe_path(sav, root_resolved):
        return
    key = resolved_key(sav)
    if key is None or key in seen_save_paths:
        return
    ensure_source(
        source_id=source_id,
        platform=platform,
        description=description,
        source_root=source_root,
        sources=sources,
        seen_source_roots=seen_source_roots,
    )
    seen_save_paths.add(key)
    saves.append(
        SaveEntry(
            platform=platform,
            source_id=source_id,
            display_name=display_name,
            path=str(sav),
        )
    )


def _rom_stems_in_dir(
    directory: Path,
    extensions: Iterable[str],
    root_resolved: Path,
    warnings: List[str],
) -> Set[str]:
    wanted = frozenset(ext.lower() for ext in extensions)
    stems: Set[str] = set()
    for child in safe_iterdir(directory, warnings):
        if (
            child.is_file()
            and child.suffix.lower() in wanted
            and is_safe_path(child, root_resolved)
        ):
            stems.add(child.stem.casefold())
    return stems


def scan_saves_subdir(
    rom_dir: Path,
    *,
    platform: str,
    source_id: str,
    description: str,
    rom_extensions: Iterable[str],
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    """Pair ``rom_dir/saves/<stem>.sav`` with a same-stem ROM in ``rom_dir``."""
    rom_stems = _rom_stems_in_dir(rom_dir, rom_extensions, root_resolved, warnings)
    if not rom_stems:
        return
    saves_dir = rom_dir / "saves"
    if not saves_dir.is_dir() or not is_safe_path(saves_dir, root_resolved):
        return
    for item in safe_iterdir(saves_dir, warnings):
        if not item.is_file() or item.suffix.lower() not in SAVE_EXTENSIONS:
            continue
        if item.stem.casefold() not in rom_stems:
            continue
        add_save(
            item,
            platform=platform,
            source_id=source_id,
            display_name=item.stem,
            source_root=rom_dir,
            description=description,
            root_resolved=root_resolved,
            sources=sources,
            saves=saves,
            seen_source_roots=seen_source_roots,
            seen_save_paths=seen_save_paths,
        )


def scan_sibling_saves_in_dir(
    directory: Path,
    *,
    platform: str,
    source_id: str,
    description: str,
    source_root: Path,
    rom_extensions: Iterable[str],
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    if not directory.is_dir() or not is_safe_path(directory, root_resolved):
        return
    rom_stems = _rom_stems_in_dir(directory, rom_extensions, root_resolved, warnings)
    if not rom_stems:
        return
    for child in safe_iterdir(directory, warnings):
        if not child.is_file() or child.suffix.lower() not in SAVE_EXTENSIONS:
            continue
        if child.stem.casefold() not in rom_stems:
            continue
        add_save(
            child,
            platform=platform,
            source_id=source_id,
            display_name=child.stem,
            source_root=source_root,
            description=description,
            root_resolved=root_resolved,
            sources=sources,
            saves=saves,
            seen_source_roots=seen_source_roots,
            seen_save_paths=seen_save_paths,
        )


def walk_sibling_depth(
    directory: Path,
    *,
    depth: int,
    max_depth: int,
    platform: str,
    source_id: str,
    description: str,
    source_root: Path,
    rom_extensions: Iterable[str],
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
    visited: Set[Path],
) -> None:
    key = resolved_key(directory)
    if key is None or key in visited:
        return
    visited.add(key)

    scan_sibling_saves_in_dir(
        directory,
        platform=platform,
        source_id=source_id,
        description=description,
        source_root=source_root,
        rom_extensions=rom_extensions,
        root_resolved=root_resolved,
        warnings=warnings,
        sources=sources,
        saves=saves,
        seen_source_roots=seen_source_roots,
        seen_save_paths=seen_save_paths,
    )
    if depth >= max_depth:
        return
    for child in safe_iterdir(directory, warnings):
        if not child.is_dir() or not is_safe_path(child, root_resolved):
            continue
        if child.name.casefold() in _SKIP_DIR_NAMES:
            continue
        walk_sibling_depth(
            child,
            depth=depth + 1,
            max_depth=max_depth,
            platform=platform,
            source_id=source_id,
            description=description,
            source_root=source_root,
            rom_extensions=rom_extensions,
            root_resolved=root_resolved,
            warnings=warnings,
            sources=sources,
            saves=saves,
            seen_source_roots=seen_source_roots,
            seen_save_paths=seen_save_paths,
            visited=visited,
        )


def _user_selected_pair(
    root: Path,
    rom_extensions: Iterable[str],
    root_resolved: Path,
    warnings: List[str],
) -> bool:
    wanted = frozenset(ext.lower() for ext in rom_extensions)
    if not has_rom(root, wanted, root_resolved, warnings):
        return False
    for child in safe_iterdir(root, warnings):
        if not child.is_file() or child.suffix.lower() not in SAVE_EXTENSIONS:
            continue
        if any((root / f"{child.stem}{ext}").is_file() for ext in wanted):
            return True
    return False


def scan_cartridge_platform(
    platform: str,
    root: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
    *,
    standard_root: bool = False,
    fingerprint_dirs: Tuple[str, ...] = (),
) -> None:
    """Scan ``roms/<platform>``, sibling saves, and optional cart fingerprints."""
    rom_extensions = supported_extensions(platform)
    if not rom_extensions:
        return
    wrapper_depth = 0 if standard_root else 2
    sibling_id = f"{platform}_r4"
    sibling_description = (
        f"{platform.upper()} sibling .sav/.srm next to {', '.join(rom_extensions)}"
    )
    saves_id = f"{platform}_saves"
    saves_description = f"{platform.upper()} saves directory next to ROM"

    pattern_dirs = collect_unique_dirs(
        [
            *find_pattern_dirs(
                root, ("roms", platform), root_resolved, warnings, wrapper_depth
            ),
            root,
        ]
    )
    extra: List[Path] = []
    if not standard_root:
        for base in [root, *safe_iterdir(root, warnings)]:
            if not base.is_dir() or not is_safe_path(base, root_resolved):
                continue
            if base.name.casefold() in _SKIP_DIR_NAMES:
                continue
            extra.append(base)
            if base is root:
                continue
            for child in safe_iterdir(base, warnings):
                if child.is_dir() and is_safe_path(child, root_resolved):
                    if child.name.casefold() not in _SKIP_DIR_NAMES:
                        extra.append(child)

    for candidate in collect_unique_dirs([*pattern_dirs, *extra]):
        scan_saves_subdir(
            candidate,
            platform=platform,
            source_id=saves_id,
            description=saves_description,
            rom_extensions=rom_extensions,
            root_resolved=root_resolved,
            warnings=warnings,
            sources=sources,
            saves=saves,
            seen_source_roots=seen_source_roots,
            seen_save_paths=seen_save_paths,
        )

    allow_sibling = (
        card_has_dirs(root, root_resolved, fingerprint_dirs)
        or is_roms_platform_path(root, platform)
        or any(
            is_roms_platform_path(path, platform)
            for path in find_pattern_dirs(
                root, ("roms", platform), root_resolved, warnings, wrapper_depth
            )
        )
        or _user_selected_pair(root, rom_extensions, root_resolved, warnings)
    )
    if not allow_sibling:
        return
    visited: Set[Path] = set()
    walk_sibling_depth(
        root,
        depth=0,
        max_depth=MAX_SIBLING_DEPTH,
        platform=platform,
        source_id=sibling_id,
        description=sibling_description,
        source_root=root,
        rom_extensions=rom_extensions,
        root_resolved=root_resolved,
        warnings=warnings,
        sources=sources,
        saves=saves,
        seen_source_roots=seen_source_roots,
        seen_save_paths=seen_save_paths,
        visited=visited,
    )

"""NDS TWiLight Menu++ and R4/Wood sibling-sav layouts."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Set

from ..models import SaveEntry, SaveSource
from ..rom_formats import supported_extensions
from .common import (
    collect_unique_dirs,
    find_pattern_dirs,
    is_safe_path,
    resolved_key,
    safe_iterdir,
)

# The set of dumps recognised as an NDS ROM.  Sourced from the same canonical
# definition the identity layer uses, so a save is only ever paired with a file
# both layers agree is a ROM (see :mod:`vajsave.rom_formats`).
_NDS_ROM_EXTENSIONS = frozenset(supported_extensions("nds"))

# Root is depth 0; a ROM/save pair sitting on the 4th nested directory below
# root is still reachable, but the walk never recurses past this bound.
_MAX_SIBLING_DEPTH = 4

# Top-level folders that hold NDS saves apart from the ROMs themselves.
_TOP_LEVEL_SAVE_DIR_NAMES = frozenset({"save", "saves"})


def _has_nds_rom(directory: Path, root_resolved: Path, warnings: List[str]) -> bool:
    for child in safe_iterdir(directory, warnings):
        if (
            child.is_file()
            and child.suffix.lower() in _NDS_ROM_EXTENSIONS
            and is_safe_path(child, root_resolved)
        ):
            return True
    return False


def _ensure_source(
    *,
    source_id: str,
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
            platform="nds",
            description=description,
            root_path=str(source_root),
        )
    )


def _add_sav_file(
    sav: Path,
    *,
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
    _ensure_source(
        source_id=source_id,
        description=description,
        source_root=source_root,
        sources=sources,
        seen_source_roots=seen_source_roots,
    )
    seen_save_paths.add(key)
    saves.append(
        SaveEntry(
            platform="nds",
            source_id=source_id,
            display_name=display_name,
            path=str(sav),
        )
    )


def _scan_twilight_dir(
    rom_dir: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    if not _has_nds_rom(rom_dir, root_resolved, warnings):
        return
    saves_dir = rom_dir / "saves"
    if not saves_dir.is_dir() or not is_safe_path(saves_dir, root_resolved):
        return
    for item in safe_iterdir(saves_dir, warnings):
        if not item.is_file() or item.suffix.lower() != ".sav":
            continue
        _add_sav_file(
            item,
            source_id="nds_twilight",
            display_name=item.stem,
            source_root=rom_dir,
            description="NDS TWiLight Menu++ saves directory",
            root_resolved=root_resolved,
            sources=sources,
            saves=saves,
            seen_source_roots=seen_source_roots,
            seen_save_paths=seen_save_paths,
        )


def _card_has_nds_fingerprint(root: Path, root_resolved: Path) -> bool:
    markers = [
        root / "_nds",
        root / "__rpg",
        root / "R4.dat",
        root / "TTMenu",
        root / "_system_",
    ]
    for m in markers:
        try:
            if m.exists() and is_safe_path(m, root_resolved):
                return True
        except OSError:
            continue
    return False


def _is_roms_nds_path(path: Path) -> bool:
    """True if path is (or is under) a roms/nds style folder name pair."""
    parts_lower = [p.lower() for p in path.parts]
    for i in range(len(parts_lower) - 1):
        if parts_lower[i] == "roms" and parts_lower[i + 1] == "nds":
            return True
    return path.name.lower() == "nds" and path.parent.name.lower() == "roms"


def _scan_sibling_sav_in_dir(
    directory: Path,
    *,
    source_root: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    if not directory.is_dir() or not is_safe_path(directory, root_resolved):
        return
    nds_stems: Set[str] = set()
    for child in safe_iterdir(directory, warnings):
        if (
            child.is_file()
            and child.suffix.lower() in _NDS_ROM_EXTENSIONS
            and is_safe_path(child, root_resolved)
        ):
            nds_stems.add(child.stem)
    if not nds_stems:
        return
    for child in safe_iterdir(directory, warnings):
        if not child.is_file() or child.suffix.lower() != ".sav":
            continue
        if child.stem not in nds_stems:
            continue
        _add_sav_file(
            child,
            source_id="nds_r4",
            display_name=child.stem,
            source_root=source_root,
            description="NDS R4/Wood sibling .sav next to .nds/.ids",
            root_resolved=root_resolved,
            sources=sources,
            saves=saves,
            seen_source_roots=seen_source_roots,
            seen_save_paths=seen_save_paths,
        )


def _walk_sibling_depth(
    directory: Path,
    *,
    depth: int,
    max_depth: int,
    source_root: Path,
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

    _scan_sibling_sav_in_dir(
        directory,
        source_root=source_root,
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
        _walk_sibling_depth(
            child,
            depth=depth + 1,
            max_depth=max_depth,
            source_root=source_root,
            root_resolved=root_resolved,
            warnings=warnings,
            sources=sources,
            saves=saves,
            seen_source_roots=seen_source_roots,
            seen_save_paths=seen_save_paths,
            visited=visited,
        )


def _collect_rom_stems(
    root: Path,
    root_resolved: Path,
    warnings: List[str],
    max_depth: int = _MAX_SIBLING_DEPTH,
) -> Dict[str, List[Path]]:
    """Index NDS ROM stems under ``root`` with a bounded, cycle-safe walk."""
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
                if child.suffix.lower() in _NDS_ROM_EXTENSIONS:
                    stems.setdefault(child.stem.casefold(), []).append(child)
            elif child.is_dir() and depth < max_depth:
                walk(child, depth + 1)

    walk(root, 0)
    return stems


def _scan_top_level_save_dirs(
    root: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    """Pair a top-level SAVE/saves folder with ROMs by unique stem.

    Wood/TWiLight cards sometimes keep the ROMs and the ``SAVE``/``saves`` folder
    apart. A ``.sav`` is only accepted when exactly one NDS ROM below root
    shares its stem, so an unrelated ``.sav`` is never claimed as an NDS save.
    """
    save_dirs = [
        child
        for child in safe_iterdir(root, warnings)
        if child.is_dir()
        and child.name.casefold() in _TOP_LEVEL_SAVE_DIR_NAMES
        and is_safe_path(child, root_resolved)
    ]
    if not save_dirs:
        return
    rom_stems = _collect_rom_stems(root, root_resolved, warnings)
    if not rom_stems:
        return
    for save_dir in save_dirs:
        for item in safe_iterdir(save_dir, warnings):
            if not item.is_file() or item.suffix.lower() != ".sav":
                continue
            if len(rom_stems.get(item.stem.casefold(), [])) != 1:
                continue
            _add_sav_file(
                item,
                source_id="nds_save_dir",
                display_name=item.stem,
                source_root=save_dir,
                description="NDS top-level SAVE/saves directory paired by ROM stem",
                root_resolved=root_resolved,
                sources=sources,
                saves=saves,
                seen_source_roots=seen_source_roots,
                seen_save_paths=seen_save_paths,
            )


def scan_nds(
    root: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    # TWiLight: dirs with NDS ROM + saves/*.sav
    # Prefer known roms/nds layout via pattern, then shallow walk for saves/ next to ROMs
    twilight_candidates = collect_unique_dirs(
        [
            *find_pattern_dirs(root, ("roms", "nds"), root_resolved, warnings),
            root,  # user may have selected the rom folder itself
        ]
    )
    # Also discover shallow: root and children/grandchildren that contain saves/ + ROM
    # without full-disk rglob of *.sav
    extra: List[Path] = []
    for base in [root, *safe_iterdir(root, warnings)]:
        if not base.is_dir() or not is_safe_path(base, root_resolved):
            continue
        extra.append(base)
        if base is root:
            continue
        for child in safe_iterdir(base, warnings):
            if child.is_dir() and is_safe_path(child, root_resolved):
                extra.append(child)
    for candidate in collect_unique_dirs([*twilight_candidates, *extra]):
        _scan_twilight_dir(
            candidate, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

    # R4/Wood sibling .sav: only with card fingerprint, roms/nds path, or user-selected dir
    allow_sibling = (
        _card_has_nds_fingerprint(root, root_resolved)
        or _is_roms_nds_path(root)
        or any(
            _is_roms_nds_path(p)
            for p in find_pattern_dirs(root, ("roms", "nds"), root_resolved, warnings)
        )
    )
    # User selected a directory that itself holds a ROM + matching .sav
    user_selected_pair = False
    if _has_nds_rom(root, root_resolved, warnings):
        for child in safe_iterdir(root, warnings):
            if not child.is_file() or child.suffix.lower() != ".sav":
                continue
            if any(
                (root / f"{child.stem}{ext}").is_file() for ext in _NDS_ROM_EXTENSIONS
            ):
                user_selected_pair = True
                break

    if allow_sibling or user_selected_pair:
        visited: Set[Path] = set()
        _walk_sibling_depth(
            root,
            depth=0,
            max_depth=_MAX_SIBLING_DEPTH,
            source_root=root,
            root_resolved=root_resolved,
            warnings=warnings,
            sources=sources,
            saves=saves,
            seen_source_roots=seen_source_roots,
            seen_save_paths=seen_save_paths,
            visited=visited,
        )

    # Top-level SAVE/saves kept apart from the ROMs: pair by unique stem only.
    _scan_top_level_save_dirs(
        root,
        root_resolved,
        warnings,
        sources,
        saves,
        seen_source_roots,
        seen_save_paths,
    )

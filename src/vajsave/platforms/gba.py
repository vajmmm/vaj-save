"""GBA flashcart save layouts (EZ-Flash / EverDrive / SuperChis SuperFW)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Set

from ..models import SaveEntry, SaveSource
from .cartridge_files import SAVER_ROM_EXTENSIONS, classify_saver_platform, collect_rom_stems
from .common import (
    collect_unique_dirs,
    find_pattern_dirs,
    is_safe_path,
    iter_save_files,
    resolved_key,
    safe_iterdir,
)

_EVERDRIVE_EXTS = frozenset({".sav", ".srm", ".fla", ".eep"})


def _add_file_save(
    *,
    path: Path,
    source_id: str,
    display_name: str,
    root_resolved: Path,
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
    source_root: Path,
    description: str,
    platform: str = "gba",
    source_platform: str | None = None,
) -> None:
    if not path.is_file() or not is_safe_path(path, root_resolved):
        return
    file_key = resolved_key(path)
    if file_key is None or file_key in seen_save_paths:
        return

    src_key = resolved_key(source_root)
    if src_key is not None and src_key not in seen_source_roots:
        seen_source_roots.add(src_key)
        sources.append(
            SaveSource(
                source_id=source_id,
                platform=source_platform or platform,
                description=description,
                root_path=str(source_root),
            )
        )

    seen_save_paths.add(file_key)
    saves.append(
        SaveEntry(
            platform=platform,
            source_id=source_id,
            display_name=display_name,
            path=str(path),
        )
    )


def _scan_ezflash_saver(
    saver_dir: Path,
    scan_root: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    rom_stems = collect_rom_stems(
        scan_root, root_resolved, warnings, SAVER_ROM_EXTENSIONS
    )
    for item in iter_save_files(saver_dir, root_resolved, warnings, frozenset({".sav"})):
        platform = classify_saver_platform(item.stem, rom_stems)
        _add_file_save(
            path=item,
            source_id="gba_ezflash",
            display_name=item.stem,
            root_resolved=root_resolved,
            sources=sources,
            saves=saves,
            seen_source_roots=seen_source_roots,
            seen_save_paths=seen_save_paths,
            source_root=saver_dir,
            description="GBA EZ-Flash SAVER directory",
            platform=platform,
            source_platform="gba",
        )


def _scan_everdrive_save(
    save_dir: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    for item in iter_save_files(save_dir, root_resolved, warnings, _EVERDRIVE_EXTS):
        _add_file_save(
            path=item,
            source_id="gba_everdrive",
            display_name=item.stem,
            root_resolved=root_resolved,
            sources=sources,
            saves=saves,
            seen_source_roots=seen_source_roots,
            seen_save_paths=seen_save_paths,
            source_root=save_dir,
            description="GBA EverDrive GBASYS/SAVE directory",
        )


def _scan_everdrive_pro_gamedata(
    gamedata_dir: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    for game_dir in safe_iterdir(gamedata_dir, warnings):
        if not game_dir.is_dir() or not is_safe_path(game_dir, root_resolved):
            continue
        for item in safe_iterdir(game_dir, warnings):
            if not item.is_file():
                continue
            # bram.* (any extension the cart uses)
            if not item.name.lower().startswith("bram"):
                continue
            _add_file_save(
                path=item,
                source_id="gba_everdrive_pro",
                display_name=game_dir.name,
                root_resolved=root_resolved,
                sources=sources,
                saves=saves,
                seen_source_roots=seen_source_roots,
                seen_save_paths=seen_save_paths,
                source_root=gamedata_dir,
                description="GBA EverDrive Pro EDGBA/gamedata directory",
            )


def _has_superfw_fingerprint(root: Path, root_resolved: Path) -> bool:
    marker = root / ".superfw"
    try:
        return marker.is_dir() and is_safe_path(marker, root_resolved)
    except OSError:
        return False


def _scan_sav_folder(
    save_dir: Path,
    *,
    source_id: str,
    description: str,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    if not save_dir.is_dir() or not is_safe_path(save_dir, root_resolved):
        return
    for item in iter_save_files(save_dir, root_resolved, warnings, frozenset({".sav"})):
        _add_file_save(
            path=item,
            source_id=source_id,
            display_name=item.stem,
            root_resolved=root_resolved,
            sources=sources,
            saves=saves,
            seen_source_roots=seen_source_roots,
            seen_save_paths=seen_save_paths,
            source_root=save_dir,
            description=description,
        )


def scan_gba(
    root: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
    *,
    standard_root: bool = False,
) -> None:
    wrapper_depth = 0 if standard_root else 2
    # EZ-Flash: SAVER/*.sav
    for saver_dir in collect_unique_dirs(
        find_pattern_dirs(root, ("SAVER",), root_resolved, warnings, wrapper_depth)
    ):
        _scan_ezflash_saver(
            saver_dir,
            root,
            root_resolved,
            warnings,
            sources,
            saves,
            seen_source_roots,
            seen_save_paths,
        )
    if root.name == "SAVER":
        _scan_ezflash_saver(
            root,
            root,
            root_resolved,
            warnings,
            sources,
            saves,
            seen_source_roots,
            seen_save_paths,
        )

    # EverDrive Mini/X5: GBASYS/SAVE/*.{sav,srm,fla,eep}
    for save_dir in collect_unique_dirs(
        find_pattern_dirs(root, ("GBASYS", "SAVE"), root_resolved, warnings, wrapper_depth)
    ):
        _scan_everdrive_save(
            save_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )
    if root.name.upper() == "SAVE" and root.parent.name.upper() == "GBASYS":
        _scan_everdrive_save(
            root, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

    # EverDrive Pro: EDGBA/gamedata/<rom>/bram.*
    for gamedata_dir in collect_unique_dirs(
        find_pattern_dirs(root, ("EDGBA", "gamedata"), root_resolved, warnings, wrapper_depth)
    ):
        _scan_everdrive_pro_gamedata(
            gamedata_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )
    if root.name.lower() == "gamedata" and root.parent.name.upper() == "EDGBA":
        _scan_everdrive_pro_gamedata(
            root, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

    # SuperChis / SuperFW: SAVEGAME/*.sav (default). SAVES/*.sav only with .superfw fingerprint.
    for savegame_dir in collect_unique_dirs(
        find_pattern_dirs(root, ("SAVEGAME",), root_resolved, warnings, wrapper_depth)
    ):
        _scan_sav_folder(
            savegame_dir,
            source_id="gba_superchis",
            description="GBA SuperChis/SuperFW SAVEGAME directory",
            root_resolved=root_resolved,
            warnings=warnings,
            sources=sources,
            saves=saves,
            seen_source_roots=seen_source_roots,
            seen_save_paths=seen_save_paths,
        )
    if root.name.upper() == "SAVEGAME":
        _scan_sav_folder(
            root,
            source_id="gba_superchis",
            description="GBA SuperChis/SuperFW SAVEGAME directory",
            root_resolved=root_resolved,
            warnings=warnings,
            sources=sources,
            saves=saves,
            seen_source_roots=seen_source_roots,
            seen_save_paths=seen_save_paths,
        )

    # SAVES/ is a generic folder name. Only take it with SuperFW fingerprint, or when
    # the user explicitly selected the SAVES directory.
    superfw = _has_superfw_fingerprint(root, root_resolved)
    selected_saves = root.name.upper() == "SAVES"
    if superfw or selected_saves:
        saves_dirs = collect_unique_dirs(
            find_pattern_dirs(root, ("SAVES",), root_resolved, warnings, wrapper_depth)
        )
        if selected_saves:
            saves_dirs = collect_unique_dirs([*saves_dirs, root])
        for saves_dir in saves_dirs:
            _scan_sav_folder(
                saves_dir,
                source_id="gba_superchis",
                description="GBA SuperChis/SuperFW SAVES directory",
                root_resolved=root_resolved,
                warnings=warnings,
                sources=sources,
                saves=saves,
                seen_source_roots=seen_source_roots,
                seen_save_paths=seen_save_paths,
            )

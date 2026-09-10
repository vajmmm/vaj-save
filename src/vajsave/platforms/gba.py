"""GBA flashcart save layouts (EZ-Flash / EverDrive)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Set

from ..models import SaveEntry, SaveSource
from .common import (
    collect_unique_dirs,
    find_pattern_dirs,
    is_safe_path,
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
                platform="gba",
                description=description,
                root_path=str(source_root),
            )
        )

    seen_save_paths.add(file_key)
    saves.append(
        SaveEntry(
            platform="gba",
            source_id=source_id,
            display_name=display_name,
            path=str(path),
        )
    )


def _scan_ezflash_saver(
    saver_dir: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    for item in safe_iterdir(saver_dir, warnings):
        if not item.is_file() or item.suffix.lower() != ".sav":
            continue
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
    for item in safe_iterdir(save_dir, warnings):
        if not item.is_file() or item.suffix.lower() not in _EVERDRIVE_EXTS:
            continue
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


def scan_gba(
    root: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    # EZ-Flash: SAVER/*.sav
    for saver_dir in collect_unique_dirs(
        find_pattern_dirs(root, ("SAVER",), root_resolved, warnings)
    ):
        _scan_ezflash_saver(
            saver_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )
    if root.name == "SAVER":
        _scan_ezflash_saver(
            root, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

    # EverDrive Mini/X5: GBASYS/SAVE/*.{sav,srm,fla,eep}
    for save_dir in collect_unique_dirs(
        find_pattern_dirs(root, ("GBASYS", "SAVE"), root_resolved, warnings)
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
        find_pattern_dirs(root, ("EDGBA", "gamedata"), root_resolved, warnings)
    ):
        _scan_everdrive_pro_gamedata(
            gamedata_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )
    if root.name.lower() == "gamedata" and root.parent.name.upper() == "EDGBA":
        _scan_everdrive_pro_gamedata(
            root, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

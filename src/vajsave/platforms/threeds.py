"""Nintendo 3DS Checkpoint / JKSM / encrypted SD scanner."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Set, Tuple

from ..models import SaveEntry, SaveSource
from .common import (
    collect_unique_dirs,
    find_pattern_dirs,
    is_safe_path,
    resolved_key,
    safe_iterdir,
)

# JKSM category folders under JKSV/
_JKSM_CATEGORIES = ("Saves", "ExtData", "SysSave")


def _parse_checkpoint_folder_name(name: str) -> Tuple[Optional[str], str]:
    parts = name.split(maxsplit=1)
    if len(parts) == 2:
        token, display = parts
        if re.match(r"^(0x[0-9a-fA-F]+|[0-9a-fA-F]{8,16})$", token):
            return token, display
    return None, name


def _scan_checkpoint_saves_dir(
    cp_dir: Path,
    *,
    source_id: str,
    platform: str,
    description: str,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    key = resolved_key(cp_dir)
    if key is None or key in seen_source_roots:
        return
    if not cp_dir.is_dir() or not is_safe_path(cp_dir, root_resolved):
        return
    seen_source_roots.add(key)
    sources.append(
        SaveSource(
            source_id=source_id,
            platform=platform,
            description=description,
            root_path=str(cp_dir),
        )
    )
    for game_dir in safe_iterdir(cp_dir, warnings):
        if not game_dir.is_dir() or not is_safe_path(game_dir, root_resolved):
            continue
        title_id, display_name = _parse_checkpoint_folder_name(game_dir.name)
        subdirs = [
            d
            for d in safe_iterdir(game_dir, warnings)
            if d.is_dir() and is_safe_path(d, root_resolved)
        ]
        if subdirs:
            for slot_dir in subdirs:
                slot_key = resolved_key(slot_dir)
                if slot_key is None or slot_key in seen_save_paths:
                    continue
                seen_save_paths.add(slot_key)
                saves.append(
                    SaveEntry(
                        platform=platform,
                        source_id=source_id,
                        title_id=title_id,
                        display_name=display_name,
                        slot=slot_dir.name,
                        path=str(slot_dir),
                    )
                )
        else:
            game_key = resolved_key(game_dir)
            if game_key is None or game_key in seen_save_paths:
                continue
            seen_save_paths.add(game_key)
            saves.append(
                SaveEntry(
                    platform=platform,
                    source_id=source_id,
                    title_id=title_id,
                    display_name=display_name,
                    path=str(game_dir),
                )
            )


def _scan_jksm_category_dir(
    cat_dir: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    """Enumerate JKSV/Saves|ExtData|SysSave/<Game>/<slot>/ as 3ds_jksm."""
    key = resolved_key(cat_dir)
    if key is None or key in seen_source_roots:
        return
    if not cat_dir.is_dir() or not is_safe_path(cat_dir, root_resolved):
        return

    game_dirs = [
        d for d in safe_iterdir(cat_dir, warnings) if d.is_dir() and is_safe_path(d, root_resolved)
    ]
    if not game_dirs:
        return

    seen_source_roots.add(key)
    sources.append(
        SaveSource(
            source_id="3ds_jksm",
            platform="3ds",
            description="3DS JKSM save backup directory",
            root_path=str(cat_dir),
        )
    )
    for game_dir in game_dirs:
        subdirs = [
            d
            for d in safe_iterdir(game_dir, warnings)
            if d.is_dir() and is_safe_path(d, root_resolved)
        ]
        if subdirs:
            for slot_dir in subdirs:
                slot_key = resolved_key(slot_dir)
                if slot_key is None or slot_key in seen_save_paths:
                    continue
                seen_save_paths.add(slot_key)
                saves.append(
                    SaveEntry(
                        platform="3ds",
                        source_id="3ds_jksm",
                        display_name=game_dir.name,
                        slot=slot_dir.name,
                        path=str(slot_dir),
                    )
                )
        else:
            game_key = resolved_key(game_dir)
            if game_key is None or game_key in seen_save_paths:
                continue
            seen_save_paths.add(game_key)
            saves.append(
                SaveEntry(
                    platform="3ds",
                    source_id="3ds_jksm",
                    display_name=game_dir.name,
                    path=str(game_dir),
                )
            )


def scan_threeds(
    root: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    # Checkpoint: .../3ds/Checkpoint/saves
    ds3_cp_dirs = collect_unique_dirs(
        find_pattern_dirs(root, ("3ds", "Checkpoint", "saves"), root_resolved, warnings)
    )
    if root.name.lower() == "saves" and root.parent.name == "Checkpoint":
        if root.parent.parent.name.lower() == "3ds":
            ds3_cp_dirs = collect_unique_dirs([*ds3_cp_dirs, root])
    elif root.name == "Checkpoint":
        if root.parent.name.lower() == "3ds":
            direct = root / "saves"
            if direct.is_dir():
                ds3_cp_dirs = collect_unique_dirs([*ds3_cp_dirs, direct])

    for ds3_cp_dir in ds3_cp_dirs:
        _scan_checkpoint_saves_dir(
            ds3_cp_dir,
            source_id="3ds_checkpoint",
            platform="3ds",
            description="3DS Checkpoint save backup directory",
            root_resolved=root_resolved,
            warnings=warnings,
            sources=sources,
            saves=saves,
            seen_source_roots=seen_source_roots,
            seen_save_paths=seen_save_paths,
        )

    # JKSM under JKSV/{Saves,ExtData,SysSave}
    jksm_dirs: List[Path] = []
    for cat in _JKSM_CATEGORIES:
        jksm_dirs.extend(find_pattern_dirs(root, ("JKSV", cat), root_resolved, warnings))
        # User selected JKSV directly
        if root.name == "JKSV":
            direct = root / cat
            if direct.is_dir():
                jksm_dirs.append(direct)
        # User selected JKSV/<Category>
        if root.name.lower() == cat.lower() and root.parent.name == "JKSV":
            jksm_dirs.append(root)

    for cat_dir in collect_unique_dirs(jksm_dirs):
        _scan_jksm_category_dir(
            cat_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

    # Encrypted Nintendo 3DS SD container (card root only, no Checkpoint)
    has_3ds_cp = any(s.source_id == "3ds_checkpoint" for s in sources)
    n3ds_dir = root / "Nintendo 3DS"
    if not has_3ds_cp and n3ds_dir.is_dir() and is_safe_path(n3ds_dir, root_resolved):
        sources.append(
            SaveSource(
                source_id="3ds_sd",
                platform="3ds",
                description="Encrypted Nintendo 3DS SD card (raw saves not directly manageable)",
                root_path=str(n3ds_dir),
                extra={"encrypted_container": True},
            )
        )

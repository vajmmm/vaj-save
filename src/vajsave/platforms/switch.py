"""Nintendo Switch Checkpoint / JKSV / SD fingerprint scanner."""

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

# 3DS JKSM (and other non-game) folders that live under JKSV/ must not be
# treated as Switch game names.
_JKSV_RESERVED_NAMES = frozenset(
    {"saves", "extdata", "syssave", "boss", "shared", "_trash_"}
)


def _parse_checkpoint_folder_name(name: str) -> Tuple[Optional[str], str]:
    """Parse Checkpoint folder name like '0x011C4 Pokemon Moon' or '0100000000010000 Super Mario Odyssey'."""
    parts = name.split(maxsplit=1)
    if len(parts) == 2:
        token, display = parts
        if re.match(r"^(0x[0-9a-fA-F]+|[0-9a-fA-F]{8,16})$", token):
            return token, display
    return None, name


def _is_jksv_reserved(name: str) -> bool:
    return name.lower() in _JKSV_RESERVED_NAMES


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


def _scan_jksv_dir(
    jksv_dir: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    key = resolved_key(jksv_dir)
    if key is None or key in seen_source_roots:
        return
    if not jksv_dir.is_dir() or not is_safe_path(jksv_dir, root_resolved):
        return
    jksv_games = [
        d
        for d in safe_iterdir(jksv_dir, warnings)
        if d.is_dir() and is_safe_path(d, root_resolved) and not _is_jksv_reserved(d.name)
    ]
    if not jksv_games:
        return
    seen_source_roots.add(key)
    sources.append(
        SaveSource(
            source_id="switch_jksv",
            platform="switch",
            description="Switch JKSV save backup directory",
            root_path=str(jksv_dir),
        )
    )
    for game_dir in jksv_games:
        level1_dirs = [
            d for d in safe_iterdir(game_dir, warnings) if d.is_dir() and is_safe_path(d, root_resolved)
        ]
        if not level1_dirs:
            game_key = resolved_key(game_dir)
            if game_key is None or game_key in seen_save_paths:
                continue
            seen_save_paths.add(game_key)
            saves.append(
                SaveEntry(
                    platform="switch",
                    source_id="switch_jksv",
                    display_name=game_dir.name,
                    path=str(game_dir),
                )
            )
        else:
            for l1 in level1_dirs:
                level2_dirs = [
                    d for d in safe_iterdir(l1, warnings) if d.is_dir() and is_safe_path(d, root_resolved)
                ]
                if level2_dirs:
                    for l2 in level2_dirs:
                        l2_key = resolved_key(l2)
                        if l2_key is None or l2_key in seen_save_paths:
                            continue
                        seen_save_paths.add(l2_key)
                        saves.append(
                            SaveEntry(
                                platform="switch",
                                source_id="switch_jksv",
                                display_name=game_dir.name,
                                user=l1.name,
                                slot=l2.name,
                                path=str(l2),
                            )
                        )
                else:
                    l1_key = resolved_key(l1)
                    if l1_key is None or l1_key in seen_save_paths:
                        continue
                    seen_save_paths.add(l1_key)
                    saves.append(
                        SaveEntry(
                            platform="switch",
                            source_id="switch_jksv",
                            display_name=game_dir.name,
                            slot=l1.name,
                            path=str(l1),
                        )
                    )


def scan_switch(
    root: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    # Checkpoint: .../switch/Checkpoint/saves
    sw_cp_dirs = collect_unique_dirs(
        find_pattern_dirs(root, ("switch", "Checkpoint", "saves"), root_resolved, warnings)
    )
    if root.name.lower() == "saves" and root.parent.name == "Checkpoint":
        if root.parent.parent.name.lower() == "switch":
            sw_cp_dirs = collect_unique_dirs([*sw_cp_dirs, root])
    elif root.name == "Checkpoint" and root.parent.name.lower() == "switch":
        direct = root / "saves"
        if direct.is_dir():
            sw_cp_dirs = collect_unique_dirs([*sw_cp_dirs, direct])

    for sw_cp_dir in sw_cp_dirs:
        _scan_checkpoint_saves_dir(
            sw_cp_dir,
            source_id="switch_checkpoint",
            platform="switch",
            description="Switch Checkpoint save backup directory",
            root_resolved=root_resolved,
            warnings=warnings,
            sources=sources,
            saves=saves,
            seen_source_roots=seen_source_roots,
            seen_save_paths=seen_save_paths,
        )

    # JKSV: .../JKSV (skip reserved 3DS JKSM folder names)
    jksv_dirs = collect_unique_dirs(find_pattern_dirs(root, ("JKSV",), root_resolved, warnings))
    if root.name == "JKSV":
        jksv_dirs = collect_unique_dirs([*jksv_dirs, root])
    for jksv_dir in jksv_dirs:
        _scan_jksv_dir(
            jksv_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

    # SD fallback: atmosphere/ or switch/ at card root only
    has_switch_saves = any(s.source_id in ("switch_checkpoint", "switch_jksv") for s in sources)
    if not has_switch_saves:
        has_atmo = (root / "atmosphere").is_dir() and is_safe_path(root / "atmosphere", root_resolved)
        has_switch_dir = (root / "switch").is_dir() and is_safe_path(root / "switch", root_resolved)
        if has_atmo or has_switch_dir:
            sources.append(
                SaveSource(
                    source_id="switch_sd",
                    platform="switch",
                    description="Switch SD card (Atmosphere/Switch directory detected, no export saves found)",
                    root_path=str(root),
                )
            )


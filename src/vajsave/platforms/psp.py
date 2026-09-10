"""PSP SaveData layout scanner."""

from __future__ import annotations

from pathlib import Path
from typing import List, Set

from ..models import SaveEntry, SaveSource
from ..sfo import parse_sfo
from .common import (
    collect_unique_dirs,
    find_param_sfo,
    find_pattern_dirs,
    is_safe_path,
    resolved_key,
    safe_iterdir,
)


def _looks_like_psp_savedata_container(path: Path, root_resolved: Path, warnings: List[str]) -> bool:
    """True if path itself is a SAVEDATA folder (by name or PARAM.SFO game children)."""
    if path.name.upper() == "SAVEDATA":
        return True
    for child in safe_iterdir(path, warnings):
        if not child.is_dir() or not is_safe_path(child, root_resolved):
            continue
        if find_param_sfo(child, root_resolved, warnings) is not None:
            return True
    return False


def _looks_like_single_psp_save(path: Path, root_resolved: Path, warnings: List[str]) -> bool:
    return find_param_sfo(path, root_resolved, warnings) is not None


def _scan_psp_savedata_dir(
    psp_dir: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    key = resolved_key(psp_dir)
    if key is None or key in seen_source_roots:
        return
    if not psp_dir.is_dir() or not is_safe_path(psp_dir, root_resolved):
        return
    seen_source_roots.add(key)
    sources.append(
        SaveSource(
            source_id="psp",
            platform="psp",
            description="PSP SaveData directory",
            root_path=str(psp_dir),
        )
    )
    for item in safe_iterdir(psp_dir, warnings):
        if not item.is_dir() or not is_safe_path(item, root_resolved):
            continue
        item_key = resolved_key(item)
        if item_key is None or item_key in seen_save_paths:
            continue
        sfo_path = find_param_sfo(item, root_resolved, warnings)
        sfo_data = parse_sfo(sfo_path) if sfo_path else {}
        title = sfo_data.get("TITLE") or item.name
        title_id = sfo_data.get("TITLE_ID") or sfo_data.get("SAVEDATA_DIRECTORY") or item.name
        seen_save_paths.add(item_key)
        saves.append(
            SaveEntry(
                platform="psp",
                source_id="psp",
                title_id=title_id,
                display_name=title,
                path=str(item),
            )
        )


def _scan_single_psp_save(
    save_dir: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    item_key = resolved_key(save_dir)
    if item_key is None or item_key in seen_save_paths:
        return
    if not save_dir.is_dir() or not is_safe_path(save_dir, root_resolved):
        return
    sfo_path = find_param_sfo(save_dir, root_resolved, warnings)
    sfo_data = parse_sfo(sfo_path) if sfo_path else {}
    title = sfo_data.get("TITLE") or save_dir.name
    title_id = sfo_data.get("TITLE_ID") or sfo_data.get("SAVEDATA_DIRECTORY") or save_dir.name

    src_key = item_key
    if src_key not in seen_source_roots:
        seen_source_roots.add(src_key)
        sources.append(
            SaveSource(
                source_id="psp",
                platform="psp",
                description="PSP SaveData directory",
                root_path=str(save_dir),
            )
        )
    seen_save_paths.add(item_key)
    saves.append(
        SaveEntry(
            platform="psp",
            source_id="psp",
            title_id=title_id,
            display_name=title,
            path=str(save_dir),
        )
    )


def scan_psp(
    root: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    psp_dirs = collect_unique_dirs(
        [
            *find_pattern_dirs(root, ("PSP", "SAVEDATA"), root_resolved, warnings),
            *find_pattern_dirs(root, ("pspemu", "PSP", "SAVEDATA"), root_resolved, warnings),
            *find_pattern_dirs(root, ("ux0", "pspemu", "PSP", "SAVEDATA"), root_resolved, warnings),
            # Selecting the PSP folder (parent of SAVEDATA)
            *find_pattern_dirs(root, ("SAVEDATA",), root_resolved, warnings, max_wrapper_depth=0),
        ]
    )
    for psp_dir in psp_dirs:
        _scan_psp_savedata_dir(
            psp_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

    if not any(s.source_id == "psp" for s in sources):
        if _looks_like_psp_savedata_container(root, root_resolved, warnings):
            _scan_psp_savedata_dir(
                root, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
            )
        elif _looks_like_single_psp_save(root, root_resolved, warnings):
            _scan_single_psp_save(
                root, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
            )

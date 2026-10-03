"""PlayStation 3 (PS3) SaveData layout scanner."""

from __future__ import annotations

from pathlib import Path
from typing import List, Set

from ..models import SaveEntry, SaveSource
from ..sfo import parse_sfo
from .common import (
    collect_unique_dirs,
    emit_scan_progress,
    find_param_sfo,
    find_pattern_dirs,
    is_safe_path,
    resolved_key,
    safe_iterdir,
)


def _looks_like_ps3_save(path: Path, root_resolved: Path, warnings: List[str]) -> bool:
    """True if directory contains PARAM.SFO and characteristic PS3 markers."""
    sfo_path = find_param_sfo(path, root_resolved, warnings)
    if sfo_path is None:
        return False
    # PARAM.PFD is the PlayStation 3/4 save data protection file, exclusive to PS3/PS4.
    for child in safe_iterdir(path, warnings):
        if child.name.upper() == "PARAM.PFD":
            return True
    sfo = parse_sfo(sfo_path)
    title_id = str(sfo.get("TITLE_ID") or path.name).upper()
    # Typical PS3 title ID prefixes: BLUS, BLES, BCUS, BCES, NPUB, NPEB, NPJA, etc.
    if any(title_id.startswith(prefix) for prefix in ("BL", "BC", "NP", "MR")):
        return True
    return False


def _looks_like_ps3_container(path: Path, root_resolved: Path, warnings: List[str]) -> bool:
    if path.name.upper() == "SAVEDATA":
        if "PS3" in (p.upper() for p in path.parts):
            return True
        for child in safe_iterdir(path, warnings):
            if child.is_dir() and _looks_like_ps3_save(child, root_resolved, warnings):
                return True
    return False


def _scan_ps3_savedata_dir(
    savedata_dir: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    key = resolved_key(savedata_dir)
    if key is None or key in seen_source_roots:
        return
    if not savedata_dir.is_dir() or not is_safe_path(savedata_dir, root_resolved):
        return
    seen_source_roots.add(key)
    sources.append(
        SaveSource(
            source_id="ps3",
            platform="ps3",
            description="PS3 SaveData directory",
            root_path=str(savedata_dir),
        )
    )
    candidates = [
        item
        for item in safe_iterdir(savedata_dir, warnings)
        if item.is_dir() and is_safe_path(item, root_resolved)
    ]
    total = len(candidates)
    for index, item in enumerate(candidates, start=1):
        item_key = resolved_key(item)
        if item_key is None or item_key in seen_save_paths:
            emit_scan_progress(f"正在扫描 PS3… {index}/{total}", index, total)
            continue
        sfo_path = find_param_sfo(item, root_resolved, warnings)
        sfo_data = parse_sfo(sfo_path) if sfo_path else {}
        title = sfo_data.get("TITLE") or sfo_data.get("SUB_TITLE") or item.name
        title_id = sfo_data.get("TITLE_ID") or sfo_data.get("SAVEDATA_DIRECTORY") or item.name
        seen_save_paths.add(item_key)
        saves.append(
            SaveEntry(
                platform="ps3",
                source_id="ps3",
                title_id=title_id,
                display_name=title,
                path=str(item),
            )
        )
        emit_scan_progress(f"正在扫描 PS3… {index}/{total}", index, total)


def _scan_single_ps3_save(
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
    title = sfo_data.get("TITLE") or sfo_data.get("SUB_TITLE") or save_dir.name
    title_id = sfo_data.get("TITLE_ID") or sfo_data.get("SAVEDATA_DIRECTORY") or save_dir.name

    if item_key not in seen_source_roots:
        seen_source_roots.add(item_key)
        sources.append(
            SaveSource(
                source_id="ps3",
                platform="ps3",
                description="PS3 SaveData directory",
                root_path=str(save_dir),
            )
        )
    seen_save_paths.add(item_key)
    saves.append(
        SaveEntry(
            platform="ps3",
            source_id="ps3",
            title_id=title_id,
            display_name=title,
            path=str(save_dir),
        )
    )


def scan_ps3(
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
    candidates: List[Path] = [
        *find_pattern_dirs(root, ("PS3", "SAVEDATA"), root_resolved, warnings, wrapper_depth),
        *find_pattern_dirs(root, ("dev_usb000", "PS3", "SAVEDATA"), root_resolved, warnings, wrapper_depth),
        *find_pattern_dirs(root, ("dev_usb001", "PS3", "SAVEDATA"), root_resolved, warnings, wrapper_depth),
    ]

    # webMAN / internal HDD structure: dev_hdd0/home/<user_id>/savedata
    for home_dir in [
        *find_pattern_dirs(root, ("dev_hdd0", "home"), root_resolved, warnings, wrapper_depth),
        *find_pattern_dirs(root, ("home",), root_resolved, warnings, wrapper_depth),
    ]:
        for user_dir in safe_iterdir(home_dir, warnings):
            if not user_dir.is_dir() or not is_safe_path(user_dir, root_resolved):
                continue
            sd = user_dir / "savedata"
            if sd.is_dir() and is_safe_path(sd, root_resolved):
                candidates.append(sd)

    ps3_dirs = collect_unique_dirs(candidates)
    for ps3_dir in ps3_dirs:
        _scan_ps3_savedata_dir(
            ps3_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

    if not any(s.source_id == "ps3" for s in sources):
        if _looks_like_ps3_container(root, root_resolved, warnings):
            _scan_ps3_savedata_dir(
                root, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
            )
        elif _looks_like_ps3_save(root, root_resolved, warnings):
            _scan_single_ps3_save(
                root, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
            )

"""PS Vita native and exported save layout scanner."""

from __future__ import annotations

from pathlib import Path
from typing import List, Set

from ..models import SaveEntry, SaveSource
from ..sfo import parse_sfo
from .common import (
    collect_unique_dirs,
    find_pattern_dirs,
    is_safe_path,
    resolved_key,
    safe_iterdir,
)


def _scan_vita_native_dir(
    vita_dir: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    key = resolved_key(vita_dir)
    if key is None or key in seen_source_roots:
        return
    if not vita_dir.is_dir() or not is_safe_path(vita_dir, root_resolved):
        return
    seen_source_roots.add(key)
    sources.append(
        SaveSource(
            source_id="vita",
            platform="vita",
            description="PS Vita native savedata directory",
            root_path=str(vita_dir),
        )
    )
    for item in safe_iterdir(vita_dir, warnings):
        if not item.is_dir() or not is_safe_path(item, root_resolved):
            continue
        item_key = resolved_key(item)
        if item_key is None or item_key in seen_save_paths:
            continue
        sfo_path = None
        sce_sys = item / "sce_sys"
        if sce_sys.is_dir() and is_safe_path(sce_sys, root_resolved):
            for child in safe_iterdir(sce_sys, warnings):
                if child.name.lower() == "param.sfo" and child.is_file():
                    sfo_path = child
                    break
        sfo_data = parse_sfo(sfo_path) if sfo_path else {}
        title = sfo_data.get("TITLE") or item.name
        title_id = sfo_data.get("TITLE_ID") or item.name
        seen_save_paths.add(item_key)
        saves.append(
            SaveEntry(
                platform="vita",
                source_id="vita",
                title_id=title_id,
                display_name=title,
                path=str(item),
            )
        )


def _scan_vita_exported_dir(
    vexp_dir: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    key = resolved_key(vexp_dir)
    if key is None or key in seen_source_roots:
        return
    if not vexp_dir.is_dir() or not is_safe_path(vexp_dir, root_resolved):
        return
    seen_source_roots.add(key)
    sources.append(
        SaveSource(
            source_id="vita_exported",
            platform="vita",
            description="Vita Save Manager decrypted export directory",
            root_path=str(vexp_dir),
        )
    )
    for item in safe_iterdir(vexp_dir, warnings):
        if not item.is_dir() or not is_safe_path(item, root_resolved):
            continue
        item_key = resolved_key(item)
        if item_key is None or item_key in seen_save_paths:
            continue
        seen_save_paths.add(item_key)
        saves.append(
            SaveEntry(
                platform="vita",
                source_id="vita_exported",
                title_id=item.name,
                display_name=item.name,
                path=str(item),
            )
        )


def scan_vita(
    root: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    vita_dirs = collect_unique_dirs(
        [
            *find_pattern_dirs(root, ("user", "00", "savedata"), root_resolved, warnings),
            *find_pattern_dirs(root, ("ux0", "user", "00", "savedata"), root_resolved, warnings),
        ]
    )
    for vita_dir in vita_dirs:
        _scan_vita_native_dir(
            vita_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

    vexp_dirs = collect_unique_dirs(
        [
            *find_pattern_dirs(root, ("data", "savegames"), root_resolved, warnings),
            *find_pattern_dirs(root, ("ux0", "data", "savegames"), root_resolved, warnings),
        ]
    )
    for vexp_dir in vexp_dirs:
        _scan_vita_exported_dir(
            vexp_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

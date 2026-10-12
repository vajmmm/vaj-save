"""Nintendo Wii save layout scanner."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Set

from ..models import SaveEntry, SaveSource
from .console_ids import normalize_wii_title_id
from .common import (
    collect_unique_dirs,
    emit_scan_progress,
    find_pattern_dirs,
    is_safe_path,
    resolved_key,
    safe_iterdir,
)

_WII_ID_REGEX = re.compile(r"^[0-9A-Za-z]{4,6}$")


def _read_title_txt(game_dir: Path, root_resolved: Path, warnings: List[str]) -> Optional[str]:
    txt_path = game_dir / "title.txt"
    if txt_path.is_file() and is_safe_path(txt_path, root_resolved):
        try:
            content = txt_path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                # Some title.txt have multiple lines; take the first non-empty line
                for line in content.splitlines():
                    if line.strip():
                        return line.strip()
        except Exception:
            pass
    return None


_WII_SAVE_FILES = ("banner.bin", "title.txt", "data.bin")


def _looks_like_wii_save(path: Path, root_resolved: Path, warnings: List[str]) -> bool:
    del root_resolved
    for child in safe_iterdir(path, warnings):
        if child.name.lower() in _WII_SAVE_FILES:
            return True
    return False


def _wii_save_target(
    item: Path, root_resolved: Path, warnings: List[str]
) -> Optional[tuple]:
    """Return ``(save_dir, title_id, title)`` or ``None``.

    A NAND title folder holds ``data`` (the save) and ``content`` (the game).
    Only ``data`` is a save. An SD export keeps ``data.bin`` in the game folder.
    """
    data_dir = item / "data"
    if data_dir.is_dir() and is_safe_path(data_dir, root_resolved):
        title_id = normalize_wii_title_id(item.name) or item.name
        title = (
            _read_title_txt(data_dir, root_resolved, warnings)
            or _read_title_txt(item, root_resolved, warnings)
            or title_id
        )
        return data_dir, title_id, title
    if _looks_like_wii_save(item, root_resolved, warnings) or _WII_ID_REGEX.match(item.name):
        names = {child.name.lower() for child in safe_iterdir(item, warnings)}
        if "content" in names and not names.intersection(_WII_SAVE_FILES):
            return None
        title_id = normalize_wii_title_id(item.name) or item.name
        title = _read_title_txt(item, root_resolved, warnings) or title_id
        return item, title_id, title
    return None


def _scan_wii_container_dir(
    container_dir: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    key = resolved_key(container_dir)
    if key is None or key in seen_source_roots:
        return
    if not container_dir.is_dir() or not is_safe_path(container_dir, root_resolved):
        return
    seen_source_roots.add(key)
    sources.append(
        SaveSource(
            source_id="wii",
            platform="wii",
            description="Wii Save directory",
            root_path=str(container_dir),
        )
    )
    candidates = [
        item
        for item in safe_iterdir(container_dir, warnings)
        if item.is_dir() and is_safe_path(item, root_resolved)
    ]
    resolved_saves = []
    for item in candidates:
        target = _wii_save_target(item, root_resolved, warnings)
        if target is not None:
            resolved_saves.append(target)
    total = len(resolved_saves)
    for index, (save_dir, title_id, title) in enumerate(resolved_saves, start=1):
        item_key = resolved_key(save_dir)
        if item_key is None or item_key in seen_save_paths:
            emit_scan_progress(f"正在扫描 Wii… {index}/{total}", index, total)
            continue
        seen_save_paths.add(item_key)
        saves.append(
            SaveEntry(
                platform="wii",
                source_id="wii",
                title_id=title_id,
                display_name=title,
                path=str(save_dir),
            )
        )
        emit_scan_progress(f"正在扫描 Wii… {index}/{total}", index, total)


def _scan_single_wii_save(
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
    if save_dir.name.lower() == "data":
        title_id = normalize_wii_title_id(save_dir.parent.name) or save_dir.parent.name
        title = _read_title_txt(save_dir, root_resolved, warnings) or title_id
    else:
        title_id = normalize_wii_title_id(save_dir.name) or save_dir.name
        title = _read_title_txt(save_dir, root_resolved, warnings) or title_id

    if item_key not in seen_source_roots:
        seen_source_roots.add(item_key)
        sources.append(
            SaveSource(
                source_id="wii",
                platform="wii",
                description="Wii Save directory",
                root_path=str(save_dir),
            )
        )
    seen_save_paths.add(item_key)
    saves.append(
        SaveEntry(
            platform="wii",
            source_id="wii",
            title_id=title_id,
            display_name=title,
            path=str(save_dir),
        )
    )


def scan_wii(
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
        *find_pattern_dirs(root, ("savegames",), root_resolved, warnings, wrapper_depth),
        *find_pattern_dirs(root, ("wiisaves",), root_resolved, warnings, wrapper_depth),
        *find_pattern_dirs(root, ("title", "00010000"), root_resolved, warnings, wrapper_depth),
        *find_pattern_dirs(root, ("title", "00010001"), root_resolved, warnings, wrapper_depth),
    ]

    wii_dirs = collect_unique_dirs(candidates)
    for w_dir in wii_dirs:
        _scan_wii_container_dir(
            w_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

    if not any(s.source_id == "wii" for s in sources):
        if _looks_like_wii_save(root, root_resolved, warnings):
            _scan_single_wii_save(
                root, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
            )

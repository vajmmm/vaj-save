"""PlayStation 4 (PS4) SaveData layout scanner."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Set

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

_CUSA_REGEX = re.compile(r"^CUSA\d{5}", re.IGNORECASE)


def _looks_like_ps4_save(path: Path, root_resolved: Path, warnings: List[str]) -> bool:
    """True if directory matches CUSA title id or contains PS4 save markers."""
    if _CUSA_REGEX.match(path.name):
        return True
    sfo = _find_ps4_sfo(path, root_resolved, warnings)
    if sfo is not None:
        data = parse_sfo(sfo)
        title_id = str(data.get("TITLE_ID") or "").upper()
        if title_id.startswith("CUSA"):
            return True
    return False


def _find_ps4_sfo(path: Path, root_resolved: Path, warnings: List[str]) -> Optional[Path]:
    """Look for param.sfo in path or path/sce_sys."""
    direct = find_param_sfo(path, root_resolved, warnings)
    if direct is not None:
        return direct
    sce_sys = path / "sce_sys"
    if sce_sys.is_dir() and is_safe_path(sce_sys, root_resolved):
        return find_param_sfo(sce_sys, root_resolved, warnings)
    return None


def _scan_ps4_games_dir(
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
            source_id="ps4",
            platform="ps4",
            description="PS4 SaveData directory",
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
            emit_scan_progress(f"正在扫描 PS4… {index}/{total}", index, total)
            continue
        sfo_path = _find_ps4_sfo(item, root_resolved, warnings)
        sfo_data = parse_sfo(sfo_path) if sfo_path else {}
        title = sfo_data.get("TITLE") or item.name
        title_id = sfo_data.get("TITLE_ID") or (item.name if _CUSA_REGEX.match(item.name) else item.name)
        seen_save_paths.add(item_key)
        saves.append(
            SaveEntry(
                platform="ps4",
                source_id="ps4",
                title_id=title_id,
                display_name=title,
                path=str(item),
            )
        )
        emit_scan_progress(f"正在扫描 PS4… {index}/{total}", index, total)


def _scan_single_ps4_save(
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
    sfo_path = _find_ps4_sfo(save_dir, root_resolved, warnings)
    sfo_data = parse_sfo(sfo_path) if sfo_path else {}
    title = sfo_data.get("TITLE") or save_dir.name
    title_id = sfo_data.get("TITLE_ID") or (save_dir.name if _CUSA_REGEX.match(save_dir.name) else save_dir.name)

    if item_key not in seen_source_roots:
        seen_source_roots.add(item_key)
        sources.append(
            SaveSource(
                source_id="ps4",
                platform="ps4",
                description="PS4 SaveData directory",
                root_path=str(save_dir),
            )
        )
    seen_save_paths.add(item_key)
    saves.append(
        SaveEntry(
            platform="ps4",
            source_id="ps4",
            title_id=title_id,
            display_name=title,
            path=str(save_dir),
        )
    )


def scan_ps4(
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
    candidates: List[Path] = []

    # GoldHEN / internal HDD: user/home/<user_id>/savedata
    for home_dir in [
        *find_pattern_dirs(root, ("user", "home"), root_resolved, warnings, wrapper_depth),
    ]:
        for user_dir in safe_iterdir(home_dir, warnings):
            if not user_dir.is_dir() or not is_safe_path(user_dir, root_resolved):
                continue
            sd = user_dir / "savedata"
            if sd.is_dir() and is_safe_path(sd, root_resolved):
                candidates.append(sd)

    # USB: PS4/SAVEDATA/<account_id>
    for usb_sd in find_pattern_dirs(root, ("PS4", "SAVEDATA"), root_resolved, warnings, wrapper_depth):
        for account_dir in safe_iterdir(usb_sd, warnings):
            if account_dir.is_dir() and is_safe_path(account_dir, root_resolved):
                candidates.append(account_dir)
        candidates.append(usb_sd)

    # Apollo save tool: data/apollo
    for apollo in find_pattern_dirs(root, ("data", "apollo"), root_resolved, warnings, wrapper_depth):
        for sub in safe_iterdir(apollo, warnings):
            if sub.is_dir() and is_safe_path(sub, root_resolved):
                candidates.append(sub)

    ps4_dirs = collect_unique_dirs(candidates)
    for ps4_dir in ps4_dirs:
        _scan_ps4_games_dir(
            ps4_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

    if not any(s.source_id == "ps4" for s in sources):
        if _looks_like_ps4_save(root, root_resolved, warnings):
            _scan_single_ps4_save(
                root, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
            )

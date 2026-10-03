"""Microsoft Xbox 360 save layout scanner."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Set, Tuple

from ..models import SaveEntry, SaveSource
from .common import (
    collect_unique_dirs,
    emit_scan_progress,
    find_pattern_dirs,
    is_safe_path,
    resolved_key,
    safe_iterdir,
)

_X360_TITLE_ID_REGEX = re.compile(r"^[0-9a-fA-F]{8}$")
_STFS_MAGICS = (b"CON ", b"LIVE", b"PIRS")


def _read_stfs_metadata(file_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """Extract (Title Name, Display Name) from an STFS save container if valid."""
    try:
        with file_path.open("rb") as f:
            header = f.read(1120)
        if len(header) < 0x460:
            return None, None
        if header[:4] not in _STFS_MAGICS:
            return None, None
        # STFS metadata block:
        # Offset 0x360: Title Name (128 bytes, UTF-16-BE)
        # Offset 0x3E0: Display Name (128 bytes, UTF-16-BE)
        title_raw = header[0x360:0x3E0].decode("utf-16-be", errors="ignore").rstrip("\x00").strip()
        disp_raw = header[0x3E0:0x460].decode("utf-16-be", errors="ignore").rstrip("\x00").strip()
        title = title_raw if (title_raw and title_raw.isprintable()) else None
        disp = disp_raw if (disp_raw and disp_raw.isprintable()) else None
        return title, disp
    except Exception:
        return None, None


def _inspect_x360_save_dir(save_dir: Path, warnings: List[str]) -> Tuple[str, str]:
    """Find title and title_id for a 00000001 save directory."""
    title_id = save_dir.parent.name if _X360_TITLE_ID_REGEX.match(save_dir.parent.name) else save_dir.name
    # Check title.txt if present
    txt_path = save_dir / "title.txt"
    if txt_path.is_file():
        try:
            content = txt_path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                return content.splitlines()[0].strip(), title_id
        except Exception:
            pass

    # Inspect STFS files inside 00000001
    for child in safe_iterdir(save_dir, warnings):
        if child.is_file():
            title, _ = _read_stfs_metadata(child)
            if title:
                return title, title_id

    return title_id, title_id


def _looks_like_x360_save(path: Path, root_resolved: Path, warnings: List[str]) -> bool:
    if path.name == "00000001":
        return True
    for child in safe_iterdir(path, warnings):
        if child.name == "00000001" and child.is_dir():
            return True
        if child.is_file() and is_safe_path(child, root_resolved):
            title, _ = _read_stfs_metadata(child)
            if title:
                return True
    return False


def _scan_x360_content_dir(
    content_dir: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    key = resolved_key(content_dir)
    if key is None or key in seen_source_roots:
        return
    if not content_dir.is_dir() or not is_safe_path(content_dir, root_resolved):
        return
    seen_source_roots.add(key)
    sources.append(
        SaveSource(
            source_id="x360",
            platform="x360",
            description="Xbox 360 Content directory",
            root_path=str(content_dir),
        )
    )

    # Content/<ProfileID>/<TitleID>/00000001
    save_candidates: List[Path] = []
    for profile in safe_iterdir(content_dir, warnings):
        if not profile.is_dir() or not is_safe_path(profile, root_resolved):
            continue
        for title_dir in safe_iterdir(profile, warnings):
            if not title_dir.is_dir() or not is_safe_path(title_dir, root_resolved):
                continue
            save_dir = title_dir / "00000001"
            if save_dir.is_dir() and is_safe_path(save_dir, root_resolved):
                save_candidates.append(save_dir)

    total = len(save_candidates)
    for index, save_dir in enumerate(save_candidates, start=1):
        item_key = resolved_key(save_dir)
        if item_key is None or item_key in seen_save_paths:
            emit_scan_progress(f"正在扫描 Xbox 360… {index}/{total}", index, total)
            continue
        title, title_id = _inspect_x360_save_dir(save_dir, warnings)
        seen_save_paths.add(item_key)
        saves.append(
            SaveEntry(
                platform="x360",
                source_id="x360",
                title_id=title_id,
                display_name=title,
                path=str(save_dir),
            )
        )
        emit_scan_progress(f"正在扫描 Xbox 360… {index}/{total}", index, total)


def _scan_single_x360_save(
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
    title, title_id = _inspect_x360_save_dir(save_dir, warnings)

    if item_key not in seen_source_roots:
        seen_source_roots.add(item_key)
        sources.append(
            SaveSource(
                source_id="x360",
                platform="x360",
                description="Xbox 360 Save directory",
                root_path=str(save_dir),
            )
        )
    seen_save_paths.add(item_key)
    saves.append(
        SaveEntry(
            platform="x360",
            source_id="x360",
            title_id=title_id,
            display_name=title,
            path=str(save_dir),
        )
    )


def scan_x360(
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
        *find_pattern_dirs(root, ("Content",), root_resolved, warnings, wrapper_depth),
        *find_pattern_dirs(root, ("Hdd1", "Content"), root_resolved, warnings, wrapper_depth),
        *find_pattern_dirs(root, ("Usb0", "Content"), root_resolved, warnings, wrapper_depth),
    ]

    x360_dirs = collect_unique_dirs(candidates)
    for c_dir in x360_dirs:
        _scan_x360_content_dir(
            c_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

    if not any(s.source_id == "x360" for s in sources):
        if _looks_like_x360_save(root, root_resolved, warnings):
            _scan_single_x360_save(
                root, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
            )

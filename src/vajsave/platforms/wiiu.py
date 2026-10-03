"""Nintendo Wii U save layout scanner."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set
import xml.etree.ElementTree as ET

from ..models import SaveEntry, SaveSource
from .common import (
    collect_unique_dirs,
    emit_scan_progress,
    find_pattern_dirs,
    is_safe_path,
    resolved_key,
    safe_iterdir,
)


def _parse_wiiu_meta_xml(xml_path: Path) -> Dict[str, str]:
    """Extract titles and product_code from a Wii U meta.xml file."""
    result: Dict[str, str] = {}
    if not xml_path.is_file():
        return result
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for tag in (
            "longname_zh",
            "longname_en",
            "longname_ja",
            "shortname_zh",
            "shortname_en",
            "shortname_ja",
            "product_code",
            "title_id",
        ):
            elem = root.find(f".//{tag}")
            if elem is not None and elem.text and elem.text.strip():
                result[tag] = elem.text.strip()
    except Exception:
        pass
    return result


def _find_wiiu_meta(game_dir: Path, root_resolved: Path, warnings: List[str]) -> Optional[Path]:
    """Look for meta/meta.xml or meta.xml in game directory."""
    meta_sub = game_dir / "meta" / "meta.xml"
    if meta_sub.is_file() and is_safe_path(meta_sub, root_resolved):
        return meta_sub
    direct = game_dir / "meta.xml"
    if direct.is_file() and is_safe_path(direct, root_resolved):
        return direct
    return None


def _looks_like_wiiu_save(path: Path, root_resolved: Path, warnings: List[str]) -> bool:
    if _find_wiiu_meta(path, root_resolved, warnings) is not None:
        return True
    # Has user/ or common/ subfolder and 8-hex or 16-hex name
    user_dir = path / "user"
    if user_dir.is_dir() and is_safe_path(user_dir, root_resolved):
        return True
    return False


def _scan_wiiu_container_dir(
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
            source_id="wiiu",
            platform="wiiu",
            description="Wii U Save directory",
            root_path=str(container_dir),
        )
    )
    candidates = [
        item
        for item in safe_iterdir(container_dir, warnings)
        if item.is_dir() and is_safe_path(item, root_resolved)
    ]
    total = len(candidates)
    for index, item in enumerate(candidates, start=1):
        item_key = resolved_key(item)
        if item_key is None or item_key in seen_save_paths:
            emit_scan_progress(f"正在扫描 Wii U… {index}/{total}", index, total)
            continue
        meta_path = _find_wiiu_meta(item, root_resolved, warnings)
        meta = _parse_wiiu_meta_xml(meta_path) if meta_path else {}
        title = (
            meta.get("longname_zh")
            or meta.get("longname_en")
            or meta.get("longname_ja")
            or meta.get("shortname_zh")
            or meta.get("shortname_en")
            or item.name
        )
        title_id = meta.get("product_code") or meta.get("title_id") or item.name
        seen_save_paths.add(item_key)
        saves.append(
            SaveEntry(
                platform="wiiu",
                source_id="wiiu",
                title_id=title_id,
                display_name=title,
                path=str(item),
            )
        )
        emit_scan_progress(f"正在扫描 Wii U… {index}/{total}", index, total)


def _scan_single_wiiu_save(
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
    meta_path = _find_wiiu_meta(save_dir, root_resolved, warnings)
    meta = _parse_wiiu_meta_xml(meta_path) if meta_path else {}
    title = (
        meta.get("longname_zh")
        or meta.get("longname_en")
        or meta.get("longname_ja")
        or save_dir.name
    )
    title_id = meta.get("product_code") or meta.get("title_id") or save_dir.name

    if item_key not in seen_source_roots:
        seen_source_roots.add(item_key)
        sources.append(
            SaveSource(
                source_id="wiiu",
                platform="wiiu",
                description="Wii U Save directory",
                root_path=str(save_dir),
            )
        )
    seen_save_paths.add(item_key)
    saves.append(
        SaveEntry(
            platform="wiiu",
            source_id="wiiu",
            title_id=title_id,
            display_name=title,
            path=str(save_dir),
        )
    )


def scan_wiiu(
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
        *find_pattern_dirs(root, ("storage_mlc", "usr", "save", "00050000"), root_resolved, warnings, wrapper_depth),
        *find_pattern_dirs(root, ("storage_usb", "usr", "save", "00050000"), root_resolved, warnings, wrapper_depth),
        *find_pattern_dirs(root, ("usr", "save", "00050000"), root_resolved, warnings, wrapper_depth),
        *find_pattern_dirs(root, ("wiiu", "backups"), root_resolved, warnings, wrapper_depth),
        *find_pattern_dirs(root, ("wiiu", "saves"), root_resolved, warnings, wrapper_depth),
    ]

    wiiu_dirs = collect_unique_dirs(candidates)
    for w_dir in wiiu_dirs:
        _scan_wiiu_container_dir(
            w_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

    if not any(s.source_id == "wiiu" for s in sources):
        if _looks_like_wiiu_save(root, root_resolved, warnings):
            _scan_single_wiiu_save(
                root, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
            )

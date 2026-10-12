"""Nintendo 3DS Checkpoint / JKSM / encrypted SD scanner."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Set, Tuple

from .. import threeds_sd
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

# Source id used when the encrypted SD card is read directly with the user's keys.
_SD_DIRECT_SOURCE = "3ds_sd_direct"


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
    *,
    standard_root: bool = False,
    keys: Optional["ThreedsKeys"] = None,
) -> None:
    scan_threeds_encrypted_sd(
        root,
        root_resolved,
        warnings,
        sources,
        saves,
        seen_source_roots,
        seen_save_paths,
        keys=keys,
    )
    wrapper_depth = 0 if standard_root else 2
    # Checkpoint: .../3ds/Checkpoint/saves
    ds3_cp_dirs = collect_unique_dirs(
        find_pattern_dirs(
            root,
            ("3ds", "Checkpoint", "saves"),
            root_resolved,
            warnings,
            wrapper_depth,
        )
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
        jksm_dirs.extend(
            find_pattern_dirs(root, ("JKSV", cat), root_resolved, warnings, wrapper_depth)
        )
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

    # Encrypted Nintendo 3DS SD container (card root only, no Checkpoint).
    # Suppressed once the direct read above has actually listed saves from it.
    has_3ds_cp = any(s.source_id == "3ds_checkpoint" for s in sources)
    has_direct = any(s.source_id == _SD_DIRECT_SOURCE for s in sources)
    if root.name == "Nintendo 3DS":
        n3ds_dir = root
    else:
        n3ds_dir = root / "Nintendo 3DS"
    if not has_3ds_cp and not has_direct and n3ds_dir.is_dir() and is_safe_path(n3ds_dir, root_resolved):
        sources.append(
            SaveSource(
                source_id="3ds_sd",
                platform="3ds",
                description="Encrypted Nintendo 3DS SD card (raw saves not directly manageable)",
                root_path=str(n3ds_dir),
                extra={"encrypted_container": True},
            )
        )


def _relative(id1_dir: Path, path: Path) -> str:
    """条目相对 ID1 目录的路径，例如 ``/title/00040000/000f7000/data/00000001.sav``。"""
    return "/" + path.relative_to(id1_dir).as_posix()


def _title_save_entries(
    id1_dir: Path,
    warnings: List[str],
) -> List[SaveEntry]:
    """``title/<类型>/<Title ID>/data/*.sav``。只进 data，绝不碰 content。"""
    entries: List[SaveEntry] = []
    title_root = id1_dir / "title"
    if not title_root.is_dir():
        return entries
    for type_dir in safe_iterdir(title_root, warnings):
        if not type_dir.is_dir():
            continue
        for title_dir in safe_iterdir(type_dir, warnings):
            if not title_dir.is_dir():
                continue
            data_dir = title_dir / "data"
            if not data_dir.is_dir():
                continue
            for save_file in safe_iterdir(data_dir, warnings):
                if not save_file.is_file() or save_file.suffix.lower() != ".sav":
                    continue
                entries.append(
                    SaveEntry(
                        platform="3ds",
                        source_id=_SD_DIRECT_SOURCE,
                        display_name=threeds_sd.display_name_for(title_dir.name),
                        path=str(save_file),
                        title_id=(type_dir.name + title_dir.name).lower(),
                        extra={
                            "encrypted_sd": True,
                            "sd_rel": _relative(id1_dir, save_file),
                            "id1": id1_dir.name,
                            "kind": "title",
                        },
                    )
                )
    return entries


def _extdata_entries(
    id1_dir: Path,
    warnings: List[str],
) -> List[SaveEntry]:
    """``extdata/<高位>/<低位>/<ExtData ID>/<文件…>``，每个 ExtData ID 一个条目。"""
    entries: List[SaveEntry] = []
    ext_root = id1_dir / "extdata"
    if not ext_root.is_dir():
        return entries
    for high_dir in safe_iterdir(ext_root, warnings):
        if not high_dir.is_dir():
            continue
        for low_dir in safe_iterdir(high_dir, warnings):
            if not low_dir.is_dir():
                continue
            for ext_dir in safe_iterdir(low_dir, warnings):
                if not ext_dir.is_dir():
                    continue
                if not any(child.is_file() for child in safe_iterdir(ext_dir, warnings)):
                    continue
                entries.append(
                    SaveEntry(
                        platform="3ds",
                        source_id=_SD_DIRECT_SOURCE,
                        display_name=threeds_sd.display_name_for(low_dir.name),
                        path=str(ext_dir),
                        title_id=(high_dir.name + low_dir.name).lower(),
                        slot=f"extdata-{ext_dir.name}",
                        extra={
                            "encrypted_sd": True,
                            "sd_rel": _relative(id1_dir, ext_dir),
                            "id1": id1_dir.name,
                            "kind": "extdata",
                        },
                    )
                )
    return entries


def scan_threeds_encrypted_sd(
    root: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
    *,
    keys: "Optional[threeds_sd.ThreedsKeys]" = None,
) -> None:
    """配置了密钥文件时，直读卡上加密的 ``Nintendo 3DS/``（只读，只列路径）。

    列表本身不解密：ID0 由 movable.sed 的 keyY 推出（卡上必须存在该目录，因此这里
    同时验证了密钥是否属于这张卡）；真正解密发生在备份/状态判定时。
    """
    if keys is None:
        return
    mark_root = root if root.name == "Nintendo 3DS" else root / "Nintendo 3DS"
    if not mark_root.is_dir() or not is_safe_path(mark_root, root_resolved):
        return

    id0_dir = mark_root / threeds_sd.id0_for(keys.key_y)
    if not id0_dir.is_dir():
        warnings.append(
            "3DS 直读：卡上没有与 movable.sed 匹配的 ID0 目录 "
            f"({threeds_sd.id0_for(keys.key_y)})，密钥文件可能来自另一台主机"
        )
        return

    for id1_dir in safe_iterdir(id0_dir, warnings):
        if not id1_dir.is_dir() or not is_safe_path(id1_dir, root_resolved):
            continue
        source_key = resolved_key(id1_dir)
        if source_key is None or source_key in seen_source_roots:
            continue
        found = [*_title_save_entries(id1_dir, warnings), *_extdata_entries(id1_dir, warnings)]
        if not found:
            continue
        seen_source_roots.add(source_key)
        sources.append(
            SaveSource(
                source_id=_SD_DIRECT_SOURCE,
                platform="3ds",
                description="3DS 加密 SD 卡直读（ID1 {}）".format(id1_dir.name),
                root_path=str(id1_dir),
                extra={"encrypted_container": True, "direct_read": True},
            )
        )
        for entry in found:
            save_key = resolved_key(Path(entry.path))
            if save_key is None or save_key in seen_save_paths:
                continue
            seen_save_paths.add(save_key)
            saves.append(entry)

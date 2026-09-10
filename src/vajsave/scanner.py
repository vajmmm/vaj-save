import re
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple, Union

from .models import SaveEntry, SaveSource, ScanResult
from .sfo import parse_sfo

# How many extra directory levels above a known layout prefix are allowed
# (e.g. backup/ or outer/inner/ wrapping JKSV or switch/Checkpoint/saves).
_MAX_WRAPPER_DEPTH = 2


def _is_safe_path(path: Path, root_resolved: Path) -> bool:
    """Ensure path does not escape the resolved root via symlinks."""
    try:
        resolved = path.resolve()
        return resolved.is_relative_to(root_resolved)
    except Exception:
        return False


def _safe_iterdir(path: Path, warnings: List[str]) -> List[Path]:
    """Safely list directory contents, catching permission/OS errors."""
    try:
        if not path.is_dir():
            return []
        return sorted(list(path.iterdir()))
    except (PermissionError, FileNotFoundError, OSError) as e:
        warnings.append(f"Cannot access directory {path}: {e}")
        return []


def _parse_checkpoint_folder_name(name: str) -> Tuple[Optional[str], str]:
    """Parse Checkpoint folder name like '0x011C4 Pokemon Moon' or '0100000000010000 Super Mario Odyssey'."""
    parts = name.split(maxsplit=1)
    if len(parts) == 2:
        token, display = parts
        # If token looks like a Title ID (starts with 0x, is hex, or contains digits)
        if re.match(r"^(0x[0-9a-fA-F]+|[0-9a-fA-F]{8,16})$", token):
            return token, display
    return None, name


def _resolved_key(path: Path) -> Optional[Path]:
    try:
        return path.resolve()
    except Exception:
        return None


def _find_pattern_dirs(
    root: Path,
    parts: Tuple[str, ...],
    root_resolved: Path,
    warnings: List[str],
    max_wrapper_depth: int = _MAX_WRAPPER_DEPTH,
) -> List[Path]:
    """Find dirs matching root/{wrappers 0..N}/parts without escaping root."""
    found: List[Path] = []
    seen: Set[Path] = set()

    def consider(candidate: Path) -> None:
        if not candidate.is_dir() or not _is_safe_path(candidate, root_resolved):
            return
        key = _resolved_key(candidate)
        if key is None or key in seen:
            return
        seen.add(key)
        found.append(candidate)

    consider(root.joinpath(*parts))

    if max_wrapper_depth < 1:
        return found

    for child in _safe_iterdir(root, warnings):
        if not child.is_dir() or not _is_safe_path(child, root_resolved):
            continue
        consider(child.joinpath(*parts))
        if max_wrapper_depth < 2:
            continue
        for grandchild in _safe_iterdir(child, warnings):
            if not grandchild.is_dir() or not _is_safe_path(grandchild, root_resolved):
                continue
            consider(grandchild.joinpath(*parts))

    return found


def _find_param_sfo(directory: Path, root_resolved: Path, warnings: List[str]) -> Optional[Path]:
    for child in _safe_iterdir(directory, warnings):
        if child.name.upper() == "PARAM.SFO" and child.is_file() and _is_safe_path(child, root_resolved):
            return child
    return None


def _collect_unique_dirs(dirs: Iterable[Path]) -> List[Path]:
    out: List[Path] = []
    seen: Set[Path] = set()
    for d in dirs:
        key = _resolved_key(d)
        if key is None or key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _looks_like_psp_savedata_container(path: Path, root_resolved: Path, warnings: List[str]) -> bool:
    """True if path itself is a SAVEDATA folder (by name or PARAM.SFO game children)."""
    if path.name.upper() == "SAVEDATA":
        return True
    for child in _safe_iterdir(path, warnings):
        if not child.is_dir() or not _is_safe_path(child, root_resolved):
            continue
        if _find_param_sfo(child, root_resolved, warnings) is not None:
            return True
    return False


def _looks_like_single_psp_save(path: Path, root_resolved: Path, warnings: List[str]) -> bool:
    return _find_param_sfo(path, root_resolved, warnings) is not None


def _scan_psp_savedata_dir(
    psp_dir: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    key = _resolved_key(psp_dir)
    if key is None or key in seen_source_roots:
        return
    if not psp_dir.is_dir() or not _is_safe_path(psp_dir, root_resolved):
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
    for item in _safe_iterdir(psp_dir, warnings):
        if not item.is_dir() or not _is_safe_path(item, root_resolved):
            continue
        item_key = _resolved_key(item)
        if item_key is None or item_key in seen_save_paths:
            continue
        sfo_path = _find_param_sfo(item, root_resolved, warnings)
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
    item_key = _resolved_key(save_dir)
    if item_key is None or item_key in seen_save_paths:
        return
    if not save_dir.is_dir() or not _is_safe_path(save_dir, root_resolved):
        return
    sfo_path = _find_param_sfo(save_dir, root_resolved, warnings)
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


def _scan_vita_native_dir(
    vita_dir: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
) -> None:
    key = _resolved_key(vita_dir)
    if key is None or key in seen_source_roots:
        return
    if not vita_dir.is_dir() or not _is_safe_path(vita_dir, root_resolved):
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
    for item in _safe_iterdir(vita_dir, warnings):
        if not item.is_dir() or not _is_safe_path(item, root_resolved):
            continue
        item_key = _resolved_key(item)
        if item_key is None or item_key in seen_save_paths:
            continue
        sfo_path = None
        sce_sys = item / "sce_sys"
        if sce_sys.is_dir() and _is_safe_path(sce_sys, root_resolved):
            for child in _safe_iterdir(sce_sys, warnings):
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
    key = _resolved_key(vexp_dir)
    if key is None or key in seen_source_roots:
        return
    if not vexp_dir.is_dir() or not _is_safe_path(vexp_dir, root_resolved):
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
    for item in _safe_iterdir(vexp_dir, warnings):
        if not item.is_dir() or not _is_safe_path(item, root_resolved):
            continue
        item_key = _resolved_key(item)
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
    key = _resolved_key(cp_dir)
    if key is None or key in seen_source_roots:
        return
    if not cp_dir.is_dir() or not _is_safe_path(cp_dir, root_resolved):
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
    for game_dir in _safe_iterdir(cp_dir, warnings):
        if not game_dir.is_dir() or not _is_safe_path(game_dir, root_resolved):
            continue
        title_id, display_name = _parse_checkpoint_folder_name(game_dir.name)
        subdirs = [
            d
            for d in _safe_iterdir(game_dir, warnings)
            if d.is_dir() and _is_safe_path(d, root_resolved)
        ]
        if subdirs:
            for slot_dir in subdirs:
                slot_key = _resolved_key(slot_dir)
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
            game_key = _resolved_key(game_dir)
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
    key = _resolved_key(jksv_dir)
    if key is None or key in seen_source_roots:
        return
    if not jksv_dir.is_dir() or not _is_safe_path(jksv_dir, root_resolved):
        return
    jksv_games = [
        d for d in _safe_iterdir(jksv_dir, warnings) if d.is_dir() and _is_safe_path(d, root_resolved)
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
            d for d in _safe_iterdir(game_dir, warnings) if d.is_dir() and _is_safe_path(d, root_resolved)
        ]
        if not level1_dirs:
            game_key = _resolved_key(game_dir)
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
                    d for d in _safe_iterdir(l1, warnings) if d.is_dir() and _is_safe_path(d, root_resolved)
                ]
                if level2_dirs:
                    for l2 in level2_dirs:
                        l2_key = _resolved_key(l2)
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
                    l1_key = _resolved_key(l1)
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


def scan(root_path: Union[Path, str]) -> ScanResult:
    """Scan a root directory (e.g. mounted USB volume or SD card) and list saves."""
    root = Path(root_path)
    warnings: List[str] = []

    try:
        if not root.exists() or not root.is_dir():
            warnings.append(f"Target path does not exist or is not a directory: {root}")
            return ScanResult(
                root_path=str(root),
                platform="unknown",
                sources=[],
                saves=[],
                warnings=warnings,
            )
        root_resolved = root.resolve()
    except Exception as e:
        warnings.append(f"Failed to access root path {root}: {e}")
        return ScanResult(
            root_path=str(root),
            platform="unknown",
            sources=[],
            saves=[],
            warnings=warnings,
        )

    sources: List[SaveSource] = []
    saves: List[SaveEntry] = []
    seen_source_roots: Set[Path] = set()
    seen_save_paths: Set[Path] = set()

    # -------------------------------------------------------------
    # 1. PSP SaveData Scan
    #    - card root + up to 2 wrapper levels: .../PSP/SAVEDATA
    #    - user selected SAVEDATA itself
    #    - user selected a single game directory
    # -------------------------------------------------------------
    psp_dirs = _collect_unique_dirs(
        [
            *_find_pattern_dirs(root, ("PSP", "SAVEDATA"), root_resolved, warnings),
            *_find_pattern_dirs(root, ("pspemu", "PSP", "SAVEDATA"), root_resolved, warnings),
            *_find_pattern_dirs(root, ("ux0", "pspemu", "PSP", "SAVEDATA"), root_resolved, warnings),
            # Selecting the PSP folder (parent of SAVEDATA)
            *_find_pattern_dirs(root, ("SAVEDATA",), root_resolved, warnings, max_wrapper_depth=0),
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

    # -------------------------------------------------------------
    # 2. PS Vita Native SaveData
    # -------------------------------------------------------------
    vita_dirs = _collect_unique_dirs(
        [
            *_find_pattern_dirs(root, ("user", "00", "savedata"), root_resolved, warnings),
            *_find_pattern_dirs(root, ("ux0", "user", "00", "savedata"), root_resolved, warnings),
        ]
    )
    for vita_dir in vita_dirs:
        _scan_vita_native_dir(
            vita_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

    # -------------------------------------------------------------
    # 3. PS Vita Exported
    # -------------------------------------------------------------
    vexp_dirs = _collect_unique_dirs(
        [
            *_find_pattern_dirs(root, ("data", "savegames"), root_resolved, warnings),
            *_find_pattern_dirs(root, ("ux0", "data", "savegames"), root_resolved, warnings),
        ]
    )
    for vexp_dir in vexp_dirs:
        _scan_vita_exported_dir(
            vexp_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

    # -------------------------------------------------------------
    # 4. Switch Checkpoint: .../switch/Checkpoint/saves
    # -------------------------------------------------------------
    sw_cp_dirs = _collect_unique_dirs(
        _find_pattern_dirs(root, ("switch", "Checkpoint", "saves"), root_resolved, warnings)
    )
    # User selected switch/Checkpoint/saves (or Checkpoint under switch) directly
    if root.name.lower() == "saves" and root.parent.name == "Checkpoint":
        if root.parent.parent.name.lower() == "switch":
            sw_cp_dirs = _collect_unique_dirs([*sw_cp_dirs, root])
    elif root.name == "Checkpoint" and root.parent.name.lower() == "switch":
        direct = root / "saves"
        if direct.is_dir():
            sw_cp_dirs = _collect_unique_dirs([*sw_cp_dirs, direct])

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

    # -------------------------------------------------------------
    # 5. Switch JKSV: .../JKSV
    # -------------------------------------------------------------
    jksv_dirs = _collect_unique_dirs(_find_pattern_dirs(root, ("JKSV",), root_resolved, warnings))
    if root.name == "JKSV":
        jksv_dirs = _collect_unique_dirs([*jksv_dirs, root])
    for jksv_dir in jksv_dirs:
        _scan_jksv_dir(
            jksv_dir, root_resolved, warnings, sources, saves, seen_source_roots, seen_save_paths
        )

    # -------------------------------------------------------------
    # 6. Switch SD Fallback: atmosphere/ or switch/ at card root only
    # -------------------------------------------------------------
    has_switch_saves = any(s.source_id in ("switch_checkpoint", "switch_jksv") for s in sources)
    if not has_switch_saves:
        has_atmo = (root / "atmosphere").is_dir() and _is_safe_path(root / "atmosphere", root_resolved)
        has_switch_dir = (root / "switch").is_dir() and _is_safe_path(root / "switch", root_resolved)
        if has_atmo or has_switch_dir:
            sources.append(
                SaveSource(
                    source_id="switch_sd",
                    platform="switch",
                    description="Switch SD card (Atmosphere/Switch directory detected, no export saves found)",
                    root_path=str(root),
                )
            )

    # -------------------------------------------------------------
    # 7. 3DS Checkpoint: .../3ds/Checkpoint/saves
    # -------------------------------------------------------------
    ds3_cp_dirs = _collect_unique_dirs(
        _find_pattern_dirs(root, ("3ds", "Checkpoint", "saves"), root_resolved, warnings)
    )
    if root.name.lower() == "saves" and root.parent.name == "Checkpoint":
        if root.parent.parent.name.lower() == "3ds":
            ds3_cp_dirs = _collect_unique_dirs([*ds3_cp_dirs, root])
    elif root.name == "Checkpoint":
        # Prefer 3ds parent when selecting Checkpoint under 3ds
        if root.parent.name.lower() == "3ds":
            direct = root / "saves"
            if direct.is_dir():
                ds3_cp_dirs = _collect_unique_dirs([*ds3_cp_dirs, direct])

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

    # -------------------------------------------------------------
    # 8. 3DS SD Encrypted Container: <root>/Nintendo 3DS/ (card root only)
    # -------------------------------------------------------------
    has_3ds_cp = any(s.source_id == "3ds_checkpoint" for s in sources)
    n3ds_dir = root / "Nintendo 3DS"
    if not has_3ds_cp and n3ds_dir.is_dir() and _is_safe_path(n3ds_dir, root_resolved):
        sources.append(
            SaveSource(
                source_id="3ds_sd",
                platform="3ds",
                description="Encrypted Nintendo 3DS SD card (raw saves not directly manageable)",
                root_path=str(n3ds_dir),
                extra={"encrypted_container": True},
            )
        )

    # -------------------------------------------------------------
    # 9. Determine Overall Platform
    # -------------------------------------------------------------
    if not sources:
        platform = "unknown"
    else:
        unique_platforms = list(dict.fromkeys(s.platform for s in sources))
        if len(unique_platforms) == 1:
            platform = unique_platforms[0]
        else:
            # If multiple platforms (e.g. Vita + PSP), prioritize vita or first detected
            platform = unique_platforms[0]

    return ScanResult(
        root_path=str(root),
        platform=platform,
        sources=sources,
        saves=saves,
        warnings=warnings,
    )

import re
from pathlib import Path
from typing import List, Optional, Set, Tuple, Union

from .models import SaveEntry, SaveSource, ScanResult
from .sfo import parse_sfo


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

    # -------------------------------------------------------------
    # 1. PSP SaveData Scan: <root>/PSP/SAVEDATA and <root>/pspemu/PSP/SAVEDATA
    # -------------------------------------------------------------
    psp_candidates = [
        root / "PSP" / "SAVEDATA",
        root / "pspemu" / "PSP" / "SAVEDATA",
        root / "ux0" / "pspemu" / "PSP" / "SAVEDATA",
    ]

    for psp_dir in psp_candidates:
        if psp_dir.is_dir() and _is_safe_path(psp_dir, root_resolved):
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

                # Check for PARAM.SFO (case-insensitive search)
                sfo_path = None
                for child in _safe_iterdir(item, warnings):
                    if child.name.upper() == "PARAM.SFO" and child.is_file():
                        sfo_path = child
                        break

                sfo_data = parse_sfo(sfo_path) if sfo_path else {}
                title = sfo_data.get("TITLE") or item.name
                title_id = sfo_data.get("TITLE_ID") or sfo_data.get("SAVEDATA_DIRECTORY") or item.name

                saves.append(
                    SaveEntry(
                        platform="psp",
                        source_id="psp",
                        title_id=title_id,
                        display_name=title,
                        path=str(item),
                    )
                )

    # -------------------------------------------------------------
    # 2. PS Vita Native SaveData: <root>/user/00/savedata and ux0/user/00/savedata
    # -------------------------------------------------------------
    vita_candidates = [
        root / "user" / "00" / "savedata",
        root / "ux0" / "user" / "00" / "savedata",
    ]

    for vita_dir in vita_candidates:
        if vita_dir.is_dir() and _is_safe_path(vita_dir, root_resolved):
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

                # Check for sce_sys/param.sfo
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

                saves.append(
                    SaveEntry(
                        platform="vita",
                        source_id="vita",
                        title_id=title_id,
                        display_name=title,
                        path=str(item),
                    )
                )

    # -------------------------------------------------------------
    # 3. PS Vita Exported: <root>/data/savegames and ux0/data/savegames
    # -------------------------------------------------------------
    vita_exp_candidates = [
        root / "data" / "savegames",
        root / "ux0" / "data" / "savegames",
    ]

    for vexp_dir in vita_exp_candidates:
        if vexp_dir.is_dir() and _is_safe_path(vexp_dir, root_resolved):
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
                saves.append(
                    SaveEntry(
                        platform="vita",
                        source_id="vita_exported",
                        title_id=item.name,
                        display_name=item.name,
                        path=str(item),
                    )
                )

    # -------------------------------------------------------------
    # 4. Switch Checkpoint: <root>/switch/Checkpoint/saves
    # -------------------------------------------------------------
    sw_cp_dir = root / "switch" / "Checkpoint" / "saves"
    if sw_cp_dir.is_dir() and _is_safe_path(sw_cp_dir, root_resolved):
        sources.append(
            SaveSource(
                source_id="switch_checkpoint",
                platform="switch",
                description="Switch Checkpoint save backup directory",
                root_path=str(sw_cp_dir),
            )
        )
        for game_dir in _safe_iterdir(sw_cp_dir, warnings):
            if not game_dir.is_dir() or not _is_safe_path(game_dir, root_resolved):
                continue
            title_id, display_name = _parse_checkpoint_folder_name(game_dir.name)
            subdirs = [d for d in _safe_iterdir(game_dir, warnings) if d.is_dir() and _is_safe_path(d, root_resolved)]
            if subdirs:
                for slot_dir in subdirs:
                    saves.append(
                        SaveEntry(
                            platform="switch",
                            source_id="switch_checkpoint",
                            title_id=title_id,
                            display_name=display_name,
                            slot=slot_dir.name,
                            path=str(slot_dir),
                        )
                    )
            else:
                saves.append(
                    SaveEntry(
                        platform="switch",
                        source_id="switch_checkpoint",
                        title_id=title_id,
                        display_name=display_name,
                        path=str(game_dir),
                    )
                )

    # -------------------------------------------------------------
    # 5. Switch JKSV: <root>/JKSV
    # -------------------------------------------------------------
    jksv_dir = root / "JKSV"
    if jksv_dir.is_dir() and _is_safe_path(jksv_dir, root_resolved):
        jksv_games = [d for d in _safe_iterdir(jksv_dir, warnings) if d.is_dir() and _is_safe_path(d, root_resolved)]
        if jksv_games:
            sources.append(
                SaveSource(
                    source_id="switch_jksv",
                    platform="switch",
                    description="Switch JKSV save backup directory",
                    root_path=str(jksv_dir),
                )
            )
            for game_dir in jksv_games:
                level1_dirs = [d for d in _safe_iterdir(game_dir, warnings) if d.is_dir() and _is_safe_path(d, root_resolved)]
                if not level1_dirs:
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
                        level2_dirs = [d for d in _safe_iterdir(l1, warnings) if d.is_dir() and _is_safe_path(d, root_resolved)]
                        if level2_dirs:
                            for l2 in level2_dirs:
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
                            saves.append(
                                SaveEntry(
                                    platform="switch",
                                    source_id="switch_jksv",
                                    display_name=game_dir.name,
                                    slot=l1.name,
                                    path=str(l1),
                                )
                            )

    # -------------------------------------------------------------
    # 6. Switch SD Fallback: atmosphere/ or switch/ exists without saves
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
    # 7. 3DS Checkpoint: <root>/3ds/Checkpoint/saves
    # -------------------------------------------------------------
    ds3_cp_dir = root / "3ds" / "Checkpoint" / "saves"
    if ds3_cp_dir.is_dir() and _is_safe_path(ds3_cp_dir, root_resolved):
        sources.append(
            SaveSource(
                source_id="3ds_checkpoint",
                platform="3ds",
                description="3DS Checkpoint save backup directory",
                root_path=str(ds3_cp_dir),
            )
        )
        for game_dir in _safe_iterdir(ds3_cp_dir, warnings):
            if not game_dir.is_dir() or not _is_safe_path(game_dir, root_resolved):
                continue
            unique_id, display_name = _parse_checkpoint_folder_name(game_dir.name)
            subdirs = [d for d in _safe_iterdir(game_dir, warnings) if d.is_dir() and _is_safe_path(d, root_resolved)]
            if subdirs:
                for slot_dir in subdirs:
                    saves.append(
                        SaveEntry(
                            platform="3ds",
                            source_id="3ds_checkpoint",
                            title_id=unique_id,
                            display_name=display_name,
                            slot=slot_dir.name,
                            path=str(slot_dir),
                        )
                    )
            else:
                saves.append(
                    SaveEntry(
                        platform="3ds",
                        source_id="3ds_checkpoint",
                        title_id=unique_id,
                        display_name=display_name,
                        path=str(game_dir),
                    )
                )

    # -------------------------------------------------------------
    # 8. 3DS SD Encrypted Container: <root>/Nintendo 3DS/
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

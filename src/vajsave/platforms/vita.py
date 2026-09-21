"""PS Vita native and exported save layout scanner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

from ..covers import MAX_COVER_BYTES, find_embedded_cover
from ..models import SaveEntry, SaveSource
from ..sfo import parse_sfo
from .common import (
    collect_unique_dirs,
    find_pattern_dirs,
    is_safe_path,
    resolved_key,
    safe_iterdir,
)


@dataclass(frozen=True)
class _VitaCompanionMetadata:
    """Title and artwork copied from the installed app metadata, when present."""

    title: str = ""
    cover_path: Optional[Path] = None


def _find_casefold_child(
    directory: Path,
    name: str,
    root_resolved: Path,
    warnings: List[str],
    *,
    want_dir: bool,
) -> Optional[Path]:
    """Find one direct child by name, tolerating case-only filesystem changes."""
    if not name:
        return None
    try:
        exact = directory / name
        matches_type = exact.is_dir() if want_dir else exact.is_file()
        if matches_type and is_safe_path(exact, root_resolved):
            return exact
    except OSError:
        pass

    folded = name.casefold()
    by_casefold: Dict[str, Path] = {}
    for child in safe_iterdir(directory, warnings):
        by_casefold.setdefault(child.name.casefold(), child)

    candidate = by_casefold.get(folded)
    if candidate is not None:
        try:
            matches_type = candidate.is_dir() if want_dir else candidate.is_file()
            if matches_type and is_safe_path(candidate, root_resolved):
                return candidate
        except OSError:
            pass
    return None


def _ancestor_dirs(path: Path, root_resolved: Path):
    """Yield a bounded set of parents that can contain a sibling ``app`` tree."""
    current = path.parent
    for _ in range(8):
        try:
            if not current.is_dir() or not is_safe_path(current, root_resolved):
                return
        except OSError:
            return
        yield current
        parent = current.parent
        if parent == current:
            return
        current = parent


def _find_vita_param_sfo(
    directory: Path, root_resolved: Path, warnings: List[str]
) -> Optional[Path]:
    """Find an application ``param.sfo`` at its standard locations."""
    sce_sys = _find_casefold_child(
        directory, "sce_sys", root_resolved, warnings, want_dir=True
    )
    for parent in (sce_sys, directory):
        if parent is None:
            continue
        found = _find_casefold_child(
            parent, "param.sfo", root_resolved, warnings, want_dir=False
        )
        if found is not None:
            return found
    return None


def _find_standard_vita_cover(directory: Path, root_resolved: Path) -> Optional[Path]:
    """Probe canonical Vita icon paths without enumerating the game directory."""
    for relative in (
        ("sce_sys", "icon0.png"),
        ("sce_sys", "ICON0.PNG"),
        ("icon0.png",),
        ("ICON0.PNG",),
    ):
        candidate = directory.joinpath(*relative)
        try:
            if (
                candidate.is_file()
                and candidate.stat().st_size <= MAX_COVER_BYTES
                and is_safe_path(candidate, root_resolved)
            ):
                return candidate
        except OSError:
            continue
    return None


def _read_vita_companion(
    directory: Path, root_resolved: Path, warnings: List[str]
) -> _VitaCompanionMetadata:
    """Read title/icon metadata from one matching app or appmeta directory."""
    sfo_path = _find_vita_param_sfo(directory, root_resolved, warnings)
    sfo_data = parse_sfo(sfo_path) if sfo_path is not None else {}
    title = str(sfo_data.get("TITLE") or "").strip()
    cover_path = _find_standard_vita_cover(directory, root_resolved)
    if cover_path is None:
        cover_path = find_embedded_cover(directory, max_depth=1)
    return _VitaCompanionMetadata(title=title, cover_path=cover_path)


def _lookup_vita_companion(
    save_dir: Path,
    title_id: str,
    root_resolved: Path,
    warnings: List[str],
    cache: Dict[str, Optional[_VitaCompanionMetadata]],
    container_cache: Optional[Dict[Path, bool]] = None,
) -> Optional[_VitaCompanionMetadata]:
    """Match a save's Title ID to a sibling ``app``/``appmeta`` directory.

    The lookup is exact and bounded: it checks only the ancestors of the save
    path and one direct child under each known companion directory. It never
    walks the installed game's files or builds a full ``app`` index.
    """
    key = str(title_id or "").strip()
    if not key:
        return None
    cache_key = key.casefold()
    if cache_key in cache:
        return cache[cache_key]

    if container_cache is None:
        container_cache = {}

    title = ""
    cover_path: Optional[Path] = None
    seen_dirs: Set[Path] = set()
    for ancestor in _ancestor_dirs(save_dir, root_resolved):
        for container_name in ("app", "appmeta"):
            # These are fixed Vita layout names. Probe them directly so every
            # save does not list the whole savedata directory just to discover
            # that its sibling app container is absent. Windows and the usual
            # Vita filesystems already handle case-insensitive lookup here.
            container = ancestor / container_name
            if container in container_cache:
                container_exists = container_cache[container]
            else:
                try:
                    container_exists = container.is_dir() and is_safe_path(
                        container, root_resolved
                    )
                except OSError:
                    container_exists = False
                container_cache[container] = container_exists
            if not container_exists:
                continue

            companion = _find_casefold_child(
                container,
                key,
                root_resolved,
                warnings,
                want_dir=True,
            )
            if companion is None:
                continue
            companion_key = resolved_key(companion)
            if companion_key is None or companion_key in seen_dirs:
                continue
            seen_dirs.add(companion_key)
            metadata = _read_vita_companion(companion, root_resolved, warnings)
            if not title and metadata.title:
                title = metadata.title
            if cover_path is None and metadata.cover_path is not None:
                cover_path = metadata.cover_path
            if title and cover_path is not None:
                break
        if title and cover_path is not None:
            break

    result = (
        _VitaCompanionMetadata(title=title, cover_path=cover_path)
        if title or cover_path is not None
        else None
    )
    cache[cache_key] = result
    return result


def _enrich_vita_entry(
    item: Path,
    title: str,
    title_id: str,
    root_resolved: Path,
    warnings: List[str],
    companion_cache: Dict[str, Optional[_VitaCompanionMetadata]],
    container_cache: Optional[Dict[Path, bool]] = None,
) -> tuple[str, Optional[Path]]:
    """Fill a Vita save's title and cover from its installed app metadata."""
    companion = _lookup_vita_companion(
        item, title_id, root_resolved, warnings, companion_cache, container_cache
    )
    display_name = companion.title or title if companion is not None else title
    if companion is not None and companion.cover_path is not None:
        return display_name, companion.cover_path
    return display_name, _find_standard_vita_cover(item, root_resolved)


def _scan_vita_native_dir(
    vita_dir: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
    companion_cache: Dict[str, Optional[_VitaCompanionMetadata]],
    container_cache: Optional[Dict[Path, bool]] = None,
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
        sfo_path = _find_vita_param_sfo(item, root_resolved, warnings)
        sfo_data = parse_sfo(sfo_path) if sfo_path else {}
        title = str(sfo_data.get("TITLE") or item.name).strip()
        title_id = str(sfo_data.get("TITLE_ID") or item.name).strip()
        title, cover_path = _enrich_vita_entry(
            item,
            title,
            title_id,
            root_resolved,
            warnings,
            companion_cache,
            container_cache,
        )
        seen_save_paths.add(item_key)
        saves.append(
            SaveEntry(
                platform="vita",
                source_id="vita",
                title_id=title_id,
                display_name=title,
                path=str(item),
                cover_path=str(cover_path) if cover_path is not None else None,
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
    companion_cache: Dict[str, Optional[_VitaCompanionMetadata]],
    container_cache: Optional[Dict[Path, bool]] = None,
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
        title_id = item.name
        title, cover_path = _enrich_vita_entry(
            item,
            title_id,
            title_id,
            root_resolved,
            warnings,
            companion_cache,
            container_cache,
        )
        seen_save_paths.add(item_key)
        saves.append(
            SaveEntry(
                platform="vita",
                source_id="vita_exported",
                title_id=title_id,
                display_name=title,
                path=str(item),
                cover_path=str(cover_path) if cover_path is not None else None,
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
    *,
    standard_root: bool = False,
) -> None:
    companion_cache: Dict[str, Optional[_VitaCompanionMetadata]] = {}
    container_cache: Dict[Path, bool] = {}
    wrapper_depth = 0 if standard_root else 2
    vita_dirs = collect_unique_dirs(
        [
            *find_pattern_dirs(
                root, ("user", "00", "savedata"), root_resolved, warnings, wrapper_depth
            ),
            *find_pattern_dirs(
                root,
                ("ux0", "user", "00", "savedata"),
                root_resolved,
                warnings,
                wrapper_depth,
            ),
        ]
    )
    for vita_dir in vita_dirs:
        _scan_vita_native_dir(
            vita_dir,
            root_resolved,
            warnings,
            sources,
            saves,
            seen_source_roots,
            seen_save_paths,
            companion_cache,
            container_cache,
        )

    vexp_dirs = collect_unique_dirs(
        [
            *find_pattern_dirs(
                root, ("data", "savegames"), root_resolved, warnings, wrapper_depth
            ),
            *find_pattern_dirs(
                root,
                ("ux0", "data", "savegames"),
                root_resolved,
                warnings,
                wrapper_depth,
            ),
        ]
    )
    for vexp_dir in vexp_dirs:
        _scan_vita_exported_dir(
            vexp_dir,
            root_resolved,
            warnings,
            sources,
            saves,
            seen_source_roots,
            seen_save_paths,
            companion_cache,
            container_cache,
        )

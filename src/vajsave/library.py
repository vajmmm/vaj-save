"""Local backup library with content-addressed version history."""

from __future__ import annotations

import copy
import json
import shutil
import stat
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .artwork import delete_game_covers
from .artwork.cleaner import (
    _cover_stems_for_game as _cover_stems,
    _delete_cover_file,
    _downloaded_cover_keys,
)
from .library_models import (
    _UNSAFE,
    BackupResult,
    Catalog,
    GameDeletion,
    GameRecord,
    SaveBackupStatus,
    Snapshot,
    game_key,
    sanitize_name,
)
from .library_status import (
    _parse_iso_datetime,
    classify_save_status,
    path_mtime_iso,
)
from .models import SaveEntry
from .persistence import atomic_write_json
from .save_tree import (
    _HASH_CACHE_MAX,
    _HASH_CHUNK,
    _cache_get,
    _cache_put,
    _copy_dir,
    _dir_file_list,
    _file_fingerprint,
    _hash_cache,
    _hash_cache_lock,
    _update_from_file,
    copy_save_tree,
    hash_tree,
)
from .settings_store import (
    APP_CONFIG_ENV,
    APP_CONFIG_NAME,
    APP_DIR_NAME,
    config_path,
    default_library_root,
    load_app_config,
    save_app_config,
)

CATALOG_NAME = "catalog.json"
SETTINGS_NAME = "settings.json"
DEFAULT_KEEP_LAST = 10

# In-process cache of parsed catalogs, keyed by (resolved catalog path,
# mtime_ns, size). The stored :class:`Catalog` is treated as immutable.
_CATALOG_CACHE_MAX = 128
_catalog_cache: Dict[Tuple[str, int, int], Catalog] = {}
_catalog_cache_lock = threading.Lock()


def destination_for(
    entry: SaveEntry,
    library_root: Path,
    when: Optional[datetime] = None,
) -> Path:
    stamp = (when or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    platform = sanitize_name(entry.platform or "unknown", "unknown")
    title = sanitize_name(entry.title_id or entry.display_name or "untitled")
    slot = sanitize_name(entry.slot or entry.user or "default", "default")
    return Path(library_root) / platform / title / slot / stamp


def catalog_path(library_root: Path) -> Path:
    return Path(library_root) / CATALOG_NAME


def _catalog_cache_key(path: Path) -> Optional[Tuple[str, int, int]]:
    """Fingerprint of the on-disk catalog; None when it is absent/unstatable."""
    try:
        st = path.stat()
        resolved = str(path.resolve())
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return (resolved, st.st_mtime_ns, st.st_size)


def _catalog_cache_store(path: Path, catalog: Catalog) -> None:
    """Cache a pristine snapshot of ``catalog`` under the file's current state."""
    key = _catalog_cache_key(path)
    if key is None:
        return
    snapshot = copy.deepcopy(catalog)
    with _catalog_cache_lock:
        if len(_catalog_cache) >= _CATALOG_CACHE_MAX:
            _catalog_cache.clear()
        _catalog_cache[key] = snapshot


def load_catalog(library_root: Path) -> Catalog:
    path = catalog_path(library_root)
    key = _catalog_cache_key(path)
    if key is None:
        return Catalog()
    with _catalog_cache_lock:
        cached = _catalog_cache.get(key)
    if cached is not None:
        # Hand out an independent copy: callers mutate the result in place
        # before ``save_catalog``, so the cached snapshot must stay pristine.
        return copy.deepcopy(cached)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return Catalog()
        catalog = Catalog.from_dict(data)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        # Never cache a failure: repairing the file (new mtime/size) must win.
        return Catalog()
    _catalog_cache_store(path, catalog)
    return copy.deepcopy(catalog)


def save_catalog(library_root: Path, catalog: Catalog) -> None:
    root = Path(library_root)
    root.mkdir(parents=True, exist_ok=True)
    path = catalog_path(root)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(catalog.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    # Refresh the cache from the just-written file so the next load is a hit and
    # never briefly reports stale data between replace() and the next stat.
    _catalog_cache_store(path, catalog)


def settings_path(library_root: Path) -> Path:
    return Path(library_root) / SETTINGS_NAME


def load_keep_last(library_root: Path) -> int:
    """Read keep_last from library settings.json; default 10; 0 means unlimited."""
    path = settings_path(library_root)
    if not path.is_file():
        return DEFAULT_KEEP_LAST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return DEFAULT_KEEP_LAST
    if not isinstance(data, dict):
        return DEFAULT_KEEP_LAST
    if "keep_last" not in data:
        return DEFAULT_KEEP_LAST
    value = data["keep_last"]
    # Reject bool (subclass of int) and non-int numbers.
    if type(value) is not int:
        return DEFAULT_KEEP_LAST
    if value < 0:
        return DEFAULT_KEEP_LAST
    return value


def parse_keep_last(value: Any) -> Optional[int]:
    """Coerce a user-supplied ``keep_last`` to a non-negative int, else None.

    Accepts an ``int`` (not ``bool``) or a base-10 digit string. ``0`` is a valid
    value meaning "unlimited"; negative numbers, signs, blank/whitespace and any
    other type are rejected so the caller can leave ``settings.json`` untouched.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text, 10)
    return None


def save_keep_last(library_root: Path, value: Any) -> bool:
    """Persist ``keep_last`` into the library ``settings.json`` atomically.

    Only a non-negative ``int`` (never a ``bool``) is written; an invalid value
    returns ``False`` and leaves the existing file untouched. Other keys already
    present are preserved. ``0`` means "unlimited".
    """
    if type(value) is not int or value < 0:
        return False
    root = Path(library_root)
    path = settings_path(root)
    data: Dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            existing = None
        if isinstance(existing, dict):
            data = existing
    data["keep_last"] = value
    return atomic_write_json(path, data)


def _is_safe_library_path(target: Path, library_root: Path) -> bool:
    """True only when target resolves strictly inside library_root."""
    try:
        root = Path(library_root).resolve()
        resolved = Path(target).resolve()
    except OSError:
        return False
    if resolved == root:
        return False
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    return True


def is_inside_library(path: Union[Path, str], library_root: Union[Path, str]) -> bool:
    """True when ``path`` resolves strictly inside ``library_root``,
    excluding transient staging caches like ``mtp-cache`` or ``ftp-cache``.

    Used to stop a backup from copying a snapshot (or any other library
    payload) back into the library tree.
    """
    try:
        target = Path(path).resolve()
        root = Path(library_root).resolve()
        if not _is_safe_library_path(target, root):
            return False
        rel = target.relative_to(root)
        if rel.parts and rel.parts[0] in ("mtp-cache", "ftp-cache"):
            return False
        return True
    except (TypeError, ValueError, OSError):
        return False


def _delete_snapshot_payload(snapshot: Snapshot, library_root: Path) -> bool:
    """Remove on-disk files for a snapshot; never touches paths outside library_root.

    Returns True when the catalog entry may be dropped (delete succeeded or path
    already missing). Returns False when the entry must be retained (unsafe path
    or delete failed with OSError).
    """
    root = Path(library_root)
    target = snapshot.absolute_path(root)
    if not _is_safe_library_path(target, root):
        return False
    try:
        resolved = target.resolve()
    except OSError:
        return False
    if not _is_safe_library_path(resolved, root):
        return False
    try:
        if not resolved.exists():
            return True
        if resolved.is_dir():
            shutil.rmtree(resolved)
        elif resolved.is_file():
            resolved.unlink()
        else:
            return False
        return True
    except OSError:
        return False


def prune_game_versions(
    game: GameRecord,
    library_root: Path,
    keep_last: Optional[int] = None,
) -> None:
    """Drop oldest in-library versions beyond keep_last. keep_last<=0 means no prune.

    Deletes disk first; catalog entry is removed only after successful delete or
    when the payload path is already missing. Unsafe/escaped paths and failed
    deletes retain their catalog entries. Newest keep_last entries are never
    candidates for removal.
    """
    if keep_last is None:
        keep_last = load_keep_last(library_root)
    if keep_last <= 0:
        return
    # Only the oldest prefix beyond keep_last is eligible; skip (retain) entries
    # that cannot be safely removed without touching the newest keep_last.
    candidates_end = len(game.versions) - keep_last
    i = 0
    while i < candidates_end:
        if _delete_snapshot_payload(game.versions[i], library_root):
            game.versions.pop(i)
            candidates_end -= 1
        else:
            i += 1


# Internal cover helpers re-exported/aliased for compatibility
_delete_game_covers = delete_game_covers


def delete_game(library_root: Path, game_id: str) -> GameDeletion:
    """Delete every local snapshot and cover for one catalog game.

    Device saves are never touched: only payloads that resolve inside
    ``library_root`` are removed. A snapshot that cannot be safely deleted keeps
    its catalog entry, so a partial failure is reported (``removed`` stays false)
    instead of silently claiming success.
    """
    root = Path(library_root)
    game_id = str(game_id or "")
    catalog = load_catalog(root)
    game = catalog.games.get(game_id)
    result = GameDeletion(game_id=game_id)
    if game is None:
        return result
    result.found = True

    retained: List[Snapshot] = []
    for snapshot in list(game.versions):
        if _delete_snapshot_payload(snapshot, root):
            result.snapshots_removed += 1
        else:
            retained.append(snapshot)
            result.snapshots_retained += 1
            result.errors.append(f"版本未删除: {snapshot.id}")

    if retained:
        game.versions = retained
    else:
        catalog.games.pop(game_id, None)
    try:
        save_catalog(root, catalog)
    except OSError as exc:
        result.errors.append(f"更新目录失败: {exc}")
        return result
    if not retained:
        result.removed = True

    covers_removed, cover_errors = delete_game_covers(root, game)
    result.covers_removed = covers_removed
    result.errors.extend(cover_errors)
    return result


def backup_save(
    entry: SaveEntry,
    library_root: Path,
    when: Optional[datetime] = None,
    *,
    identity_key: Optional[str] = None,
) -> BackupResult:
    """Create a new version, or reuse an existing one if content is identical.

    ``identity_key`` is the ROM identity resolved by the caller. It is stored on
    the :class:`GameRecord` so browsing the local library can hit covers cached
    under ``covers/<platform>/<sha1(identity_key)>.png``.
    """
    root = Path(library_root)
    digest = hash_tree(Path(entry.path))
    catalog = load_catalog(root)
    key = game_key(entry)
    resolved_key = str(identity_key).strip() if identity_key else None
    game = catalog.games.get(key)
    catalog_dirty = False
    if game is None:
        game = GameRecord(
            id=key,
            platform=entry.platform or "unknown",
            title_id=entry.title_id or "",
            display_name=entry.display_name,
            versions=[],
            identity_key=resolved_key,
        )
        catalog.games[key] = game
    elif resolved_key and not game.identity_key:
        game.identity_key = resolved_key
        catalog_dirty = True
    existing = game.find_hash(digest)
    if existing is not None:
        if catalog_dirty:
            save_catalog(root, catalog)
        return BackupResult(game=game, snapshot=existing, is_new=False, path=existing.absolute_path(root))

    now = when or datetime.now()
    stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    dest_dir = destination_for(entry, root, now)
    copy_save_tree(Path(entry.path), dest_dir)
    snapshot = Snapshot(
        id=stamp,
        created_at=now.isoformat(timespec="seconds"),
        sha256=digest,
        source_path=entry.path,
        path=dest_dir.relative_to(root).as_posix(),
        slot=entry.slot,
        user=entry.user,
    )
    game.versions.append(snapshot)
    if entry.display_name:
        game.display_name = entry.display_name
    keep_last = load_keep_last(root)
    prune_game_versions(game, root, keep_last)
    save_catalog(root, catalog)
    return BackupResult(game=game, snapshot=snapshot, is_new=True, path=dest_dir)


def import_save(
    entry: SaveEntry,
    library_root: Path,
    when: Optional[datetime] = None,
) -> Path:
    return backup_save(entry, library_root, when).path


def restore_snapshot(snapshot: Snapshot, library_root: Path, destination: Path) -> Path:
    """Copy a snapshot to destination. Does not modify the library copy."""
    source = snapshot.absolute_path(library_root)
    if not source.exists():
        raise FileNotFoundError(f"版本目录不存在: {source}")
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        _copy_dir(source, dest / source.name)
        return dest / source.name
    return copy_save_tree(source, dest)


def versions_for(catalog: Catalog, entry: SaveEntry) -> List[Snapshot]:
    game = catalog.games.get(game_key(entry))
    if game is None:
        return []
    return list(game.versions)


LIBRARY_SOURCE_ID = "library"


def latest_snapshot(game: GameRecord) -> Optional[Snapshot]:
    """Newest snapshot of ``game`` (catalog order is oldest -> newest)."""
    return game.versions[-1] if game.versions else None


def game_recency(game: GameRecord) -> str:
    """Sort key: ISO timestamp of the newest backup (empty when never backed up)."""
    latest = latest_snapshot(game)
    return latest.created_at if latest is not None else ""


def library_game_slot(game_id: str) -> Optional[str]:
    """Recover the slot component from a catalog id ``platform:title:slot``."""
    parts = str(game_id or "").split(":")
    if len(parts) == 3:
        return parts[2]
    return None


def game_entry(game: GameRecord, library_root: Union[Path, str]) -> SaveEntry:
    """A synthetic save row representing one catalog game for library browsing."""
    root = Path(library_root)
    latest = latest_snapshot(game)
    if latest is not None:
        path = str(latest.absolute_path(root))
    else:
        path = str(root / game.id)
    extra: Dict[str, Any] = {"library_game_id": game.id}
    identity_key = getattr(game, "identity_key", None)
    if identity_key:
        extra["identity_key"] = identity_key
    return SaveEntry(
        platform=game.platform or "unknown",
        source_id=LIBRARY_SOURCE_ID,
        display_name=game.display_name or game.title_id or game.id,
        path=path,
        title_id=game.title_id or None,
        slot=library_game_slot(game.id),
        extra=extra,
    )


def catalog_entries(catalog: Catalog, library_root: Union[Path, str]) -> List[SaveEntry]:
    """One row per catalog game, most recently backed up first."""
    games = sorted(catalog.games.values(), key=game_recency, reverse=True)
    return [game_entry(game, library_root) for game in games]


def ensure_game(catalog: Catalog, entry: SaveEntry) -> GameRecord:
    key = game_key(entry)
    game = catalog.games.get(key)
    if game is None:
        game = GameRecord(
            id=key,
            platform=entry.platform or "unknown",
            title_id=entry.title_id or "",
            display_name=entry.display_name,
            versions=[],
        )
        catalog.games[key] = game
    return game


def set_game_meta(
    library_root: Path,
    entry: SaveEntry,
    starred: Optional[bool] = None,
    note: Optional[str] = None,
) -> GameRecord:
    catalog = load_catalog(library_root)
    game = ensure_game(catalog, entry)
    if starred is not None:
        game.starred = starred
    if note is not None:
        game.note = note
    if entry.display_name:
        game.display_name = entry.display_name
    save_catalog(library_root, catalog)
    return game


def export_snapshot_zip(
    snapshot: Snapshot,
    library_root: Path,
    zip_path: Path,
    game: Optional[GameRecord] = None,
) -> Path:
    from .library_import import export_snapshot_zip as _export_snapshot_zip

    return _export_snapshot_zip(snapshot, library_root, zip_path, game=game)


def collection_stats(library_root: Path) -> Dict[str, int]:
    catalog = load_catalog(library_root)
    return {
        "games": len(catalog.games),
        "versions": sum(len(game.versions) for game in catalog.games.values()),
        "starred": sum(1 for game in catalog.games.values() if game.starred),
    }


__all__ = [
    "APP_CONFIG_ENV",
    "APP_CONFIG_NAME",
    "APP_DIR_NAME",
    "CATALOG_NAME",
    "DEFAULT_KEEP_LAST",
    "LIBRARY_SOURCE_ID",
    "SETTINGS_NAME",
    "BackupResult",
    "Catalog",
    "GameDeletion",
    "GameRecord",
    "SaveBackupStatus",
    "Snapshot",
    "backup_save",
    "catalog_entries",
    "catalog_path",
    "classify_save_status",
    "collection_stats",
    "config_path",
    "copy_save_tree",
    "default_library_root",
    "delete_game",
    "destination_for",
    "ensure_game",
    "export_snapshot_zip",
    "game_entry",
    "game_key",
    "game_recency",
    "hash_tree",
    "import_save",
    "is_inside_library",
    "latest_snapshot",
    "library_game_slot",
    "load_app_config",
    "load_catalog",
    "load_keep_last",
    "parse_keep_last",
    "path_mtime_iso",
    "prune_game_versions",
    "restore_snapshot",
    "sanitize_name",
    "save_app_config",
    "save_catalog",
    "save_keep_last",
    "set_game_meta",
    "settings_path",
    "versions_for",
]

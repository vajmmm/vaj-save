"""Local backup library with content-addressed version history."""

from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import sys
import threading
import uuid
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


def destination_slot_dir(entry: SaveEntry, library_root: Path) -> Path:
    platform = sanitize_name(entry.platform or "unknown", "unknown")
    title = sanitize_name(entry.title_id or entry.display_name or "untitled")
    slot = sanitize_name(entry.slot or entry.user or "default", "default")
    return Path(library_root) / platform / title / slot


def destination_for(
    entry: SaveEntry,
    library_root: Path,
    when: Optional[datetime] = None,
    *,
    stamp: Optional[str] = None,
) -> Path:
    if stamp is None:
        stamp = (when or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    return destination_slot_dir(entry, library_root) / stamp


def generate_unique_snapshot_id(
    game: GameRecord,
    entry: SaveEntry,
    library_root: Path,
    when: Optional[datetime] = None,
) -> Tuple[str, Path]:
    now = when or datetime.now()
    base_stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    existing_ids = {v.id for v in game.versions}
    slot_dir = destination_slot_dir(entry, library_root)
    stamp = base_stamp
    counter = 1
    while stamp in existing_ids or (slot_dir / stamp).exists():
        stamp = f"{base_stamp}_{counter}"
        counter += 1
    dest_dir = slot_dir / stamp
    return stamp, dest_dir


def cleanup_staging(library_root: Path, max_age_seconds: int = 3600) -> None:
    """Clean up stale staging folders in .staging."""
    staging_dir = Path(library_root) / ".staging"
    if not staging_dir.is_dir():
        return
    now_ts = datetime.now().timestamp()
    try:
        for item in staging_dir.iterdir():
            try:
                if item.is_dir():
                    mtime = item.stat().st_mtime
                    if now_ts - mtime > max_age_seconds:
                        shutil.rmtree(item, ignore_errors=True)
            except OSError:
                pass
    except OSError:
        pass


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


def peek_game_attr(library_root: Path, game_id: str, attr: str, default: Any) -> Any:
    """Read one catalog field without copying the catalog.

    Gallery painting asks this for every visible case. ``load_catalog`` deep-copies
    the whole library on each call, which stalls the UI once a large Vita card has
    filled the catalog. The cached snapshot is treated as immutable; only the
    requested field is returned.
    """
    path = catalog_path(library_root)
    key = _catalog_cache_key(path)
    catalog: Optional[Catalog] = None
    if key is not None:
        with _catalog_cache_lock:
            catalog = _catalog_cache.get(key)
    if catalog is None:
        catalog = load_catalog(library_root)
    game = catalog.games.get(game_id) if catalog is not None else None
    if game is None:
        return default
    return getattr(game, attr, default)


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
        if rel.parts and rel.parts[0] in ("mtp-cache", "ftp-cache", ".staging"):
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
) -> int:
    """Drop oldest un-locked in-library versions beyond keep_last. keep_last<=0 means no prune.

    Locked versions (snapshot.locked is True) are protected and are NEVER auto-pruned.
    Returns the number of pruned versions.
    """
    if keep_last is None:
        keep_last = load_keep_last(library_root)
    if keep_last <= 0:
        return 0

    unlocked = [v for v in game.versions if not getattr(v, "locked", False)]
    if len(unlocked) <= keep_last:
        return 0

    to_prune = unlocked[:-keep_last]
    pruned_count = 0
    for snapshot in to_prune:
        if _delete_snapshot_payload(snapshot, library_root):
            if snapshot in game.versions:
                game.versions.remove(snapshot)
                pruned_count += 1
    return pruned_count


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
    source_override: Optional[Path] = None,
) -> BackupResult:
    """Create a new version, or reuse an existing one if content is identical.

    ``identity_key`` is the ROM identity resolved by the caller. It is stored on
    the :class:`GameRecord` so browsing the local library can hit covers cached
    under ``covers/<platform>/<sha1(identity_key)>.png``. A missing key backfills
    an existing record; an unresolved key (``None``) never erases a stored one,
    and the catalog id itself (:func:`game_key`) is never derived from it.

    ``source_override`` decouples what gets hashed and copied from what the entry
    points at. It exists for saves that are stored encrypted on the device (the
    3DS ``Nintendo 3DS/`` card): the payload is decrypted to a local path first
    and copied from there, while ``snapshot.source_path`` still records the real
    device path.
    """
    root = Path(library_root)
    source = Path(source_override) if source_override is not None else Path(entry.path)
    if not source.exists():
        raise FileNotFoundError(f"存档路径不存在: {source}")
    digest = hash_tree(source)
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

    # P0: A -> B -> A history fix: deduplicate ONLY against the latest snapshot!
    latest = latest_snapshot(game)
    if latest is not None and latest.sha256 == digest:
        if catalog_dirty:
            save_catalog(root, catalog)
        return BackupResult(game=game, snapshot=latest, is_new=False, path=latest.absolute_path(root))

    cleanup_staging(root)

    now = when or datetime.now()
    stamp, dest_dir = generate_unique_snapshot_id(game, entry, root, now)

    # Atomic Staging: copy to .staging first and verify copy integrity
    staging_base = root / ".staging"
    staging_base.mkdir(parents=True, exist_ok=True)
    stage_dir = staging_base / f"{stamp}_{uuid.uuid4().hex[:8]}"
    stage_dir.mkdir(parents=True, exist_ok=True)

    try:
        copy_save_tree(source, stage_dir)
        staged_target = stage_dir / source.name

        staged_digest = hash_tree(staged_target)
        source_check = hash_tree(source)
        if staged_digest != digest or staged_digest != source_check:
            raise ValueError("备份源在复制过程中被修改或数据校验失败，已取消备份")

        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(stage_dir), str(dest_dir))
    except Exception as exc:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise exc

    snapshot = Snapshot(
        id=stamp,
        created_at=now.isoformat(timespec="seconds"),
        sha256=staged_digest,
        source_path=entry.path,
        path=dest_dir.relative_to(root).as_posix(),
        slot=entry.slot,
        user=entry.user,
        locked=False,
        note="",
    )
    game.versions.append(snapshot)
    if entry.display_name:
        game.display_name = entry.display_name

    # Atomic catalog commit: if save_catalog fails, remove dest_dir to prevent orphan
    try:
        save_catalog(root, catalog)
    except Exception as exc:
        if dest_dir.exists():
            shutil.rmtree(dest_dir, ignore_errors=True)
        if snapshot in game.versions:
            game.versions.remove(snapshot)
        raise exc

    # Only prune old versions AFTER the new version is safely committed to catalog
    keep_last = load_keep_last(root)
    pruned = prune_game_versions(game, root, keep_last)
    if pruned > 0:
        try:
            save_catalog(root, catalog)
        except OSError:
            pass

    return BackupResult(game=game, snapshot=snapshot, is_new=True, path=dest_dir)


def import_save(
    entry: SaveEntry,
    library_root: Path,
    when: Optional[datetime] = None,
) -> Path:
    return backup_save(entry, library_root, when).path


def restore_snapshot(snapshot: Snapshot, library_root: Path, destination: Path) -> Path:
    """Copy a snapshot to destination safely with rollback on failure.

    Guarantees:
    1. If target directory already exists, it is backed up to enable rollback.
    2. Overwriting non-empty target leaves NO orphaned leftover files.
    3. Failure during restore rolls back target safely.
    4. Does not modify the library copy.
    """
    source = snapshot.absolute_path(library_root)
    if not source.exists():
        raise FileNotFoundError(f"版本目录不存在: {source}")
    dest = Path(destination)
    if dest.name == source.name:
        target = dest
        parent = dest.parent
    else:
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / source.name
        parent = dest

    parent.mkdir(parents=True, exist_ok=True)
    stage_dir = parent / f".tmp_restore_{uuid.uuid4().hex[:8]}"

    try:
        if source.is_dir():
            _copy_dir(source, stage_dir)
        else:
            stage_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, stage_dir / source.name, follow_symlinks=False)
            stage_dir = stage_dir / source.name
    except Exception as exc:
        if stage_dir.is_dir():
            shutil.rmtree(stage_dir, ignore_errors=True)
        elif stage_dir.exists():
            stage_dir.unlink(missing_ok=True)
        raise exc

    has_existing = target.exists() and (not target.is_dir() or any(target.iterdir()))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = parent / f"{target.name}.backup_before_restore_{stamp}"

    if has_existing:
        try:
            os.replace(str(target), str(backup_path))
        except Exception as exc:
            if stage_dir.is_dir():
                shutil.rmtree(stage_dir, ignore_errors=True)
            elif stage_dir.exists():
                stage_dir.unlink(missing_ok=True)
            raise exc

    try:
        os.replace(str(stage_dir), str(target))
    except Exception as exc:
        if has_existing and backup_path.exists() and not target.exists():
            try:
                os.replace(str(backup_path), str(target))
            except Exception:
                pass
        if stage_dir.is_dir():
            shutil.rmtree(stage_dir, ignore_errors=True)
        elif stage_dir.exists():
            stage_dir.unlink(missing_ok=True)
        raise exc

    if has_existing and backup_path.exists():
        try:
            setattr(target, "backup_path", backup_path)
        except Exception:
            pass

    return target


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
    cleared: Optional[bool] = None,
) -> GameRecord:
    catalog = load_catalog(library_root)
    game = ensure_game(catalog, entry)
    if starred is not None:
        game.starred = starred
    if note is not None:
        game.note = note
    if cleared is not None:
        game.cleared = cleared
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
        "cleared": sum(1 for game in catalog.games.values() if game.cleared),
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
    "cleanup_staging",
    "collection_stats",
    "config_path",
    "copy_save_tree",
    "default_library_root",
    "delete_game",
    "destination_for",
    "destination_slot_dir",
    "ensure_game",
    "export_snapshot_zip",
    "game_entry",
    "game_key",
    "game_recency",
    "generate_unique_snapshot_id",
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
    "peek_game_attr",
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

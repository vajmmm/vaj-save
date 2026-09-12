"""Local backup library with content-addressed version history."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .models import SaveEntry

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
CATALOG_NAME = "catalog.json"
SETTINGS_NAME = "settings.json"
APP_CONFIG_NAME = "config.json"
APP_DIR_NAME = "vaj-save"
APP_CONFIG_ENV = "VAJSAVE_CONFIG_PATH"
DEFAULT_KEEP_LAST = 10
_HASH_CHUNK = 1024 * 1024
_HASH_CACHE_MAX = 2048
_hash_cache: Dict[Tuple[Any, ...], str] = {}
_hash_cache_lock = threading.Lock()


def default_library_root() -> Path:
    return Path.home() / "Documents" / "vaj-save"


def config_path() -> Path:
    """Location of the application config file (never inside the library root).

    ``VAJSAVE_CONFIG_PATH`` overrides everything (used by tests).
    """
    override = os.environ.get(APP_CONFIG_ENV)
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        base_path = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return base_path / APP_DIR_NAME / APP_CONFIG_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME / APP_CONFIG_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base_path = Path(xdg) if xdg else Path.home() / ".config"
    return base_path / APP_DIR_NAME / APP_CONFIG_NAME


def load_app_config() -> Dict[str, Any]:
    """Read the app config. Missing/corrupt/unreadable files degrade to {}."""
    path = config_path()
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_app_config(config: Dict[str, Any]) -> bool:
    """Persist the app config atomically. Returns False on write failure."""
    path = config_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except (OSError, TypeError, ValueError):
        # TypeError/ValueError guard against a non-serialisable caller payload;
        # drop any half-written temp file so it cannot linger next to the config.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def sanitize_name(name: str, fallback: str = "untitled") -> str:
    cleaned = _UNSAFE.sub("_", (name or "").strip()).strip(" .")
    return cleaned or fallback


def game_key(entry: SaveEntry) -> str:
    platform = sanitize_name(entry.platform or "unknown", "unknown")
    title = sanitize_name(entry.title_id or entry.display_name or "untitled")
    slot = sanitize_name(entry.slot or "default", "default")
    return f"{platform}:{title}:{slot}"


def destination_for(
    entry: SaveEntry,
    library_root: Path,
    when: Optional[datetime] = None,
) -> Path:
    stamp = (when or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    platform = sanitize_name(entry.platform or "unknown", "unknown")
    title = sanitize_name(entry.title_id or entry.display_name or "untitled")
    slot = sanitize_name(entry.slot or "default", "default")
    return Path(library_root) / platform / title / slot / stamp


def _update_from_file(digest: Any, path: Path) -> None:
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)


def _file_fingerprint(path: Path) -> Tuple[Any, ...]:
    stat = path.stat()
    return ("file", str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def _dir_file_list(root: Path) -> List[Tuple[Path, str, int, int]]:
    files: List[Tuple[Path, str, int, int]] = []
    for child in root.rglob("*"):
        if child.is_symlink() or not child.is_file():
            continue
        rel = child.relative_to(root).as_posix()
        stat = child.stat()
        files.append((child, rel, stat.st_mtime_ns, stat.st_size))
    files.sort(key=lambda item: item[1])
    return files


def _cache_get(key: Tuple[Any, ...]) -> Optional[str]:
    with _hash_cache_lock:
        return _hash_cache.get(key)


def _cache_put(key: Tuple[Any, ...], value: str) -> None:
    with _hash_cache_lock:
        if len(_hash_cache) >= _HASH_CACHE_MAX:
            _hash_cache.clear()
        _hash_cache[key] = value


def hash_tree(path: Path) -> str:
    """Stable sha256 of a file or directory (skips symlinks). Streamed; stat-cacheable."""
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(f"存档路径不存在: {root}")
    if root.is_symlink():
        raise ValueError(f"跳过符号链接: {root}")
    if root.is_file():
        cache_key = _file_fingerprint(root)
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        digest = hashlib.sha256()
        digest.update(b"file\0")
        _update_from_file(digest, root)
        hexdigest = digest.hexdigest()
        _cache_put(cache_key, hexdigest)
        return hexdigest

    listed = _dir_file_list(root)
    cache_key = ("dir", str(root.resolve()), tuple((rel, mtime, size) for _p, rel, mtime, size in listed))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    for child, rel, _mtime, size in listed:
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        _update_from_file(digest, child)
    hexdigest = digest.hexdigest()
    _cache_put(cache_key, hexdigest)
    return hexdigest


def copy_save_tree(source: Path, dest_dir: Path) -> Path:
    """Copy a file or directory into dest_dir, skipping symlinks."""
    source = Path(source)
    dest_dir = Path(dest_dir)
    if not source.exists():
        raise FileNotFoundError(f"存档路径不存在: {source}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / source.name
    if source.is_symlink():
        raise ValueError(f"跳过符号链接: {source}")
    if source.is_file():
        shutil.copyfile(source, target, follow_symlinks=False)
        return target
    if not source.is_dir():
        raise ValueError(f"无法复制: {source}")
    _copy_dir(source, target)
    return target


def _copy_dir(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir()):
        if child.is_symlink():
            continue
        next_dest = dest / child.name
        if child.is_dir():
            _copy_dir(child, next_dest)
        elif child.is_file():
            shutil.copyfile(child, next_dest, follow_symlinks=False)


@dataclass
class Snapshot:
    id: str
    created_at: str
    sha256: str
    source_path: str
    path: str
    slot: Optional[str] = None
    user: Optional[str] = None

    def absolute_path(self, library_root: Path) -> Path:
        return Path(library_root) / self.path


@dataclass
class GameRecord:
    id: str
    platform: str
    title_id: str
    display_name: str
    versions: List[Snapshot] = field(default_factory=list)
    starred: bool = False
    note: str = ""
    # ROM identity key (``<platform>:sha1:<hex>`` / ``<platform>:<title_id>`` ...)
    # captured when the backup ran, so browsing the local library can hit the
    # identity-hash cover cache without re-resolving against a device. ``None``
    # for legacy records: a missing key is never fabricated, so an unidentified
    # game keeps its placeholder cover.
    identity_key: Optional[str] = None

    def find_hash(self, digest: str) -> Optional[Snapshot]:
        for snap in self.versions:
            if snap.sha256 == digest:
                return snap
        return None


@dataclass
class BackupResult:
    game: GameRecord
    snapshot: Snapshot
    is_new: bool
    path: Path


@dataclass
class SaveBackupStatus:
    """Compare a live save against the latest library snapshot only."""

    status: str  # "new" | "changed" | "unchanged"
    source_mtime: Optional[str] = None
    last_backup_at: Optional[str] = None
    mtime_stale: bool = False
    sha256: Optional[str] = None


def path_mtime_iso(path: Union[Path, str]) -> Optional[str]:
    """ISO mtime of the path itself (file or directory root; no tree walk)."""
    try:
        target = Path(path)
        if not target.exists():
            return None
        return datetime.fromtimestamp(target.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return None


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def classify_save_status(
    entry: SaveEntry,
    catalog: Catalog,
    *,
    digest: Optional[str] = None,
    hash_error: bool = False,
) -> SaveBackupStatus:
    """Classify entry vs latest snapshot hash. mtime only annotates changed saves."""
    source_mtime = path_mtime_iso(entry.path)
    game = catalog.games.get(game_key(entry))
    latest: Optional[Snapshot] = game.versions[-1] if game and game.versions else None
    last_backup_at = latest.created_at if latest else None

    if hash_error:
        # Hash failed: keep row actionable as new; do not infer from history.
        return SaveBackupStatus(
            status="new",
            source_mtime=source_mtime,
            last_backup_at=last_backup_at,
            mtime_stale=False,
            sha256=None,
        )

    if latest is None:
        return SaveBackupStatus(
            status="new",
            source_mtime=source_mtime,
            last_backup_at=None,
            mtime_stale=False,
            sha256=digest,
        )

    if digest is None:
        try:
            digest = hash_tree(Path(entry.path))
        except (OSError, ValueError, FileNotFoundError):
            return SaveBackupStatus(
                status="new",
                source_mtime=source_mtime,
                last_backup_at=last_backup_at,
                mtime_stale=False,
                sha256=None,
            )

    if digest == latest.sha256:
        # Content match wins; ignore mtime jitter on FAT/USB.
        return SaveBackupStatus(
            status="unchanged",
            source_mtime=source_mtime,
            last_backup_at=last_backup_at,
            mtime_stale=False,
            sha256=digest,
        )

    mtime_stale = False
    src_dt = _parse_iso_datetime(source_mtime)
    bak_dt = _parse_iso_datetime(last_backup_at)
    if src_dt is not None and bak_dt is not None and src_dt < bak_dt:
        mtime_stale = True

    return SaveBackupStatus(
        status="changed",
        source_mtime=source_mtime,
        last_backup_at=last_backup_at,
        mtime_stale=mtime_stale,
        sha256=digest,
    )


class Catalog:
    def __init__(self, games: Optional[Dict[str, GameRecord]] = None) -> None:
        self.games: Dict[str, GameRecord] = games or {}

    def to_dict(self) -> Dict[str, Any]:
        games: List[Dict[str, Any]] = []
        for game in self.games.values():
            data = asdict(game)
            # Keep catalogs clean for games whose identity is unknown yet.
            if data.get("identity_key") is None:
                data.pop("identity_key", None)
            games.append(data)
        return {"version": 1, "games": games}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Catalog":
        games: Dict[str, GameRecord] = {}
        for raw in data.get("games") or []:
            versions = []
            for item in raw.get("versions") or []:
                versions.append(
                    Snapshot(
                        id=item.get("id") or "",
                        created_at=item.get("created_at") or "",
                        sha256=item.get("sha256") or "",
                        source_path=item.get("source_path") or "",
                        path=item.get("path") or "",
                        slot=item.get("slot"),
                        user=item.get("user"),
                    )
                )
            raw_key = raw.get("identity_key")
            record = GameRecord(
                id=raw["id"],
                platform=raw.get("platform") or "unknown",
                title_id=raw.get("title_id") or "",
                display_name=raw.get("display_name") or "",
                versions=versions,
                starred=bool(raw.get("starred")),
                note=str(raw.get("note") or ""),
                identity_key=(str(raw_key).strip() or None) if raw_key else None,
            )
            games[record.id] = record
        return cls(games)


def catalog_path(library_root: Path) -> Path:
    return Path(library_root) / CATALOG_NAME


def load_catalog(library_root: Path) -> Catalog:
    path = catalog_path(library_root)
    if not path.is_file():
        return Catalog()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return Catalog()
        return Catalog.from_dict(data)
    except (OSError, json.JSONDecodeError):
        return Catalog()


def save_catalog(library_root: Path, catalog: Catalog) -> None:
    root = Path(library_root)
    root.mkdir(parents=True, exist_ok=True)
    path = catalog_path(root)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(catalog.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


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
    """True when ``path`` resolves strictly inside ``library_root``.

    Used to stop a backup from copying a snapshot (or any other library
    payload) back into the library tree.
    """
    try:
        return _is_safe_library_path(Path(path), Path(library_root))
    except (TypeError, ValueError):
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


def prune_game_versions(game: GameRecord, library_root: Path, keep_last: int) -> None:
    """Drop oldest in-library versions beyond keep_last. keep_last<=0 means no prune.

    Deletes disk first; catalog entry is removed only after successful delete or
    when the payload path is already missing. Unsafe/escaped paths and failed
    deletes retain their catalog entries. Newest keep_last entries are never
    candidates for removal.
    """
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
    under ``covers/<platform>/<sha1(identity_key)>.png``. A missing key backfills
    an existing record; an unresolved key (``None``) never erases a stored one,
    and the catalog id itself (:func:`game_key`) is never derived from it.
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
        # Backfill a missing key on an existing record, but never overwrite a
        # stored key (with None or a different value): the recorded identity is
        # what already-cached covers are keyed by.
        game.identity_key = resolved_key
        catalog_dirty = True
    existing = game.find_hash(digest)
    if existing is not None:
        # Identical content reuses the snapshot and must not prune. A backfilled
        # identity_key still has to be persisted.
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


# --- local library browsing -------------------------------------------------

# ``source_id`` stamped on synthetic rows that represent a catalog game rather
# than a live device save.
LIBRARY_SOURCE_ID = "library"


def latest_snapshot(game: GameRecord) -> Optional[Snapshot]:
    """Newest snapshot of ``game`` (catalog order is oldest -> newest)."""
    return game.versions[-1] if game.versions else None


def game_recency(game: GameRecord) -> str:
    """Sort key: ISO timestamp of the newest backup (empty when never backed up)."""
    latest = latest_snapshot(game)
    return latest.created_at if latest is not None else ""


def library_game_slot(game_id: str) -> Optional[str]:
    """Recover the slot component from a catalog id ``platform:title:slot``.

    ``game_key`` sanitizes every component (colons are stripped), so a catalog id
    always splits into exactly three parts.
    """
    parts = str(game_id or "").split(":")
    if len(parts) == 3:
        return parts[2]
    return None


def game_entry(game: GameRecord, library_root: Union[Path, str]) -> SaveEntry:
    """A synthetic save row representing one catalog game for library browsing.

    The row points at the newest snapshot (so versions/export/restore operate on
    real library payload) and carries the catalog id in ``extra`` so every
    lookup keeps resolving against the same :class:`GameRecord`.
    """
    root = Path(library_root)
    latest = latest_snapshot(game)
    if latest is not None:
        path = str(latest.absolute_path(root))
    else:
        path = str(root / game.id)
    extra: Dict[str, Any] = {"library_game_id": game.id}
    identity_key = getattr(game, "identity_key", None)
    if identity_key:
        # Carried on the row so cover/metadata lookups keep using the ROM
        # identity the cache was written under, without a per-row catalog read.
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


def export_snapshot_zip(snapshot: Snapshot, library_root: Path, zip_path: Path) -> Path:
    import shutil

    source = snapshot.absolute_path(library_root)
    if not source.exists():
        raise FileNotFoundError(f"版本目录不存在: {source}")
    zip_path = Path(zip_path)
    if zip_path.suffix.lower() != ".zip":
        zip_path = zip_path.with_suffix(".zip")
    base = zip_path.with_suffix("")
    shutil.make_archive(str(base), "zip", root_dir=source)
    return Path(str(base) + ".zip")


def collection_stats(library_root: Path) -> Dict[str, int]:
    catalog = load_catalog(library_root)
    return {
        "games": len(catalog.games),
        "versions": sum(len(game.versions) for game in catalog.games.values()),
        "starred": sum(1 for game in catalog.games.values() if game.starred),
    }

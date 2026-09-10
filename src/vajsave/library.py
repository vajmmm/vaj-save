"""Local backup library with content-addressed version history."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .models import SaveEntry

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
CATALOG_NAME = "catalog.json"
SETTINGS_NAME = "settings.json"
DEFAULT_KEEP_LAST = 10


def default_library_root() -> Path:
    return Path.home() / "Documents" / "vaj-save"


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


def hash_tree(path: Path) -> str:
    """Stable sha256 of a file or directory (skips symlinks)."""
    root = Path(path)
    digest = hashlib.sha256()
    if not root.exists():
        raise FileNotFoundError(f"存档路径不存在: {root}")
    if root.is_symlink():
        raise ValueError(f"跳过符号链接: {root}")
    if root.is_file():
        digest.update(b"file\0")
        digest.update(root.read_bytes())
        return digest.hexdigest()
    files: List[Path] = []
    for child in root.rglob("*"):
        if child.is_symlink() or not child.is_file():
            continue
        files.append(child)
    for child in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        rel = child.relative_to(root).as_posix().encode("utf-8")
        data = child.read_bytes()
        digest.update(rel)
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
    return digest.hexdigest()


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
        target.write_bytes(source.read_bytes())
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
            next_dest.write_bytes(child.read_bytes())


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

    if latest is None:
        return SaveBackupStatus(
            status="new",
            source_mtime=source_mtime,
            last_backup_at=None,
            mtime_stale=False,
            sha256=digest,
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
        return {
            "version": 1,
            "games": [asdict(game) for game in self.games.values()],
        }

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
            record = GameRecord(
                id=raw["id"],
                platform=raw.get("platform") or "unknown",
                title_id=raw.get("title_id") or "",
                display_name=raw.get("display_name") or "",
                versions=versions,
                starred=bool(raw.get("starred")),
                note=str(raw.get("note") or ""),
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
) -> BackupResult:
    """Create a new version, or reuse an existing one if content is identical."""
    root = Path(library_root)
    digest = hash_tree(Path(entry.path))
    catalog = load_catalog(root)
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
    existing = game.find_hash(digest)
    if existing is not None:
        # Identical content reuses the snapshot and must not prune.
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

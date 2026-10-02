"""Save status classification against catalog history."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from .library_models import Catalog, SaveBackupStatus, Snapshot, game_key
from .models import SaveEntry
from .save_tree import hash_tree


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

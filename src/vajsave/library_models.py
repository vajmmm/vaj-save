"""Core domain models for the vajsave local backup library."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import SaveEntry

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_name(name: str, fallback: str = "untitled") -> str:
    cleaned = _UNSAFE.sub("_", (name or "").strip()).strip(" .")
    return cleaned or fallback


def game_key(entry: SaveEntry) -> str:
    platform = sanitize_name(entry.platform or "unknown", "unknown")
    title = sanitize_name(entry.title_id or entry.display_name or "untitled")
    slot = sanitize_name(entry.slot or entry.user or "default", "default")
    return f"{platform}:{title}:{slot}"


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
class GameDeletion:
    """Outcome of deleting one catalog game from the local library.

    ``ok`` is only true when the catalog entry was fully removed and nothing
    failed; a partial failure keeps the affected snapshots (and the catalog
    entry) so the delete is never reported as a full success.
    """

    game_id: str
    found: bool = False
    removed: bool = False
    snapshots_removed: int = 0
    snapshots_retained: int = 0
    covers_removed: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.found and self.removed and not self.errors


@dataclass
class SaveBackupStatus:
    """Compare a live save against the latest library snapshot only."""

    status: str  # "new" | "changed" | "unchanged"
    source_mtime: Optional[str] = None
    last_backup_at: Optional[str] = None
    mtime_stale: bool = False
    sha256: Optional[str] = None


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

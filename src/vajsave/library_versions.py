"""Per-version snapshot deletion for the local library."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .library import _delete_snapshot_payload, delete_game, load_catalog, save_catalog


@dataclass
class SnapshotDeletion:
    """Outcome of deleting one catalog snapshot.

    ``ok`` is only true when the snapshot was found, removed, and nothing
    failed. Deleting the last remaining version also removes the game
    (covers, note, star) via :func:`delete_game`.
    """

    game_id: str
    snapshot_id: str
    found: bool = False
    removed: bool = False
    game_removed: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.found and self.removed and not self.errors


def delete_snapshot(library_root: Path, game_id: str, snapshot_id: str) -> SnapshotDeletion:
    """Delete one in-library snapshot. Last remaining version removes the game.

    Only payloads that resolve inside ``library_root`` are removed. An unsafe
    path or ``OSError`` keeps the catalog entry and records an error. Device
    files are never touched.
    """
    root = Path(library_root)
    game_id = str(game_id or "")
    snapshot_id = str(snapshot_id or "")
    result = SnapshotDeletion(game_id=game_id, snapshot_id=snapshot_id)
    catalog = load_catalog(root)
    game = catalog.games.get(game_id)
    if game is None:
        return result
    snapshot = next((snap for snap in game.versions if snap.id == snapshot_id), None)
    if snapshot is None:
        return result
    result.found = True

    if len(game.versions) == 1:
        game_result = delete_game(root, game_id)
        result.removed = game_result.removed
        result.game_removed = game_result.removed
        result.errors.extend(game_result.errors)
        return result

    if not _delete_snapshot_payload(snapshot, root):
        result.errors.append(f"版本未删除: {snapshot.id}")
        return result

    game.versions.remove(snapshot)
    try:
        save_catalog(root, catalog)
    except OSError as exc:
        result.errors.append(f"更新目录失败: {exc}")
        return result
    result.removed = True
    return result

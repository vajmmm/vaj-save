"""Cleanup of user and downloaded covers when a game is removed."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Tuple

from ..library_models import sanitize_name
from .cache import CoverCache
from .embedded import IMAGE_EXTENSIONS
from .paths import DOWNLOADED_COVER_DIR


def _delete_cover_file(path: Path, covers_root: Path) -> bool:
    """Delete a cover file only when it resolves strictly inside ``covers_root``."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        root_resolved = Path(covers_root).resolve()
        resolved = path.resolve()
    except OSError:
        return False
    if resolved == root_resolved:
        return False
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return False
    try:
        resolved.unlink()
    except OSError:
        return False
    return True


def _cover_stems_for_game(game: Any) -> List[str]:
    """User-cover file-name stems for a game (title id, then display name)."""
    stems: List[str] = []
    for raw in (getattr(game, "title_id", None), getattr(game, "display_name", None)):
        cleaned = sanitize_name(raw or "", "").strip(" .")
        if cleaned and cleaned.lower() not in {stem.lower() for stem in stems}:
            stems.append(cleaned)
    return stems


def _downloaded_cover_keys(game: Any, cache: Any) -> List[str]:
    """Identity keys whose downloaded cover belongs to ``game``."""
    identity_key = getattr(game, "identity_key", None)
    if identity_key:
        return [identity_key]
    title = str(getattr(game, "display_name", "") or "").strip().lower()
    platform = str(getattr(game, "platform", "") or "").strip()
    if not title:
        return []
    matches = set()
    for key, record in cache.manifest().items():
        if str(record.get("platform", "")).strip() != platform:
            continue
        canonical = str(record.get("canonical_title", "")).strip().lower()
        if canonical and canonical == title:
            matches.add(str(key))
    return list(matches) if len(matches) == 1 else []


def delete_game_covers(library_root: Path, game: Any) -> Tuple[List[str], List[str]]:
    """Remove this game's downloaded + user covers; return (removed, errors)."""
    removed: List[str] = []
    errors: List[str] = []
    covers_root = Path(library_root) / DOWNLOADED_COVER_DIR
    try:
        cache = CoverCache(covers_root)
        for key in _downloaded_cover_keys(game, cache):
            for path in cache.remove(getattr(game, "platform", None), key):
                removed.append(str(path))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"封面删除失败: {exc}")
    directory = covers_root / sanitize_name(getattr(game, "platform", None) or "unknown", "unknown")
    targets = {
        f"{stem}.{ext}".lower()
        for stem in _cover_stems_for_game(game)
        for ext in IMAGE_EXTENSIONS
    }
    if targets:
        try:
            entries = list(directory.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            if entry.name.lower() not in targets:
                continue
            try:
                if _delete_cover_file(entry, covers_root):
                    removed.append(str(entry))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"用户封面删除失败: {exc}")
    return removed, errors

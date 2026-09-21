"""Backup, restore, export, delete, notes, stars, and visible-list helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Union

from .backup_jobs import run_selected_backups
from .jobs import CancelToken
from .library import (
    Catalog,
    GameDeletion,
    SaveBackupStatus,
    Snapshot,
    catalog_entries,
    classify_save_status,
    collection_stats,
    backup_save,
    delete_game,
    export_snapshot_zip,
    game_key,
    is_inside_library,
    load_catalog,
    latest_snapshot,
    path_mtime_iso,
    parse_keep_last,
    restore_snapshot,
    save_keep_last,
    set_game_meta,
    versions_for,
)
from .models import SaveEntry
from .platforms.catalog import PLATFORM_LABELS, PLATFORM_ORDER
from .scan_session import _hash_tree, build_status_text

if TYPE_CHECKING:
    from .app_state import AppState


class LibraryActions:
    def __init__(self, app: AppState) -> None:
        self.app = app

    def set_library_root(self, library_root: Union[Path, str]) -> Path:
        """Switch the local backup library and persist it to the app config.

        Clears the cached backup statuses; when a scan result is present the
        statuses are recomputed against the new library.
        """
        app = self.app
        app.library_root = Path(library_root).expanduser()
        app._backup_statuses = {}
        app._identity_resolver = None
        app._metadata_service = None
        app._artwork_service = None
        app.settings.update(library_root=str(app.library_root))
        if app.current_result is not None:
            app.refresh_backup_statuses()
            app.status_text = build_status_text(
                app.current_result, counts=app.backup_status_counts()
            )
        else:
            app.status_text = f"备份库路径已更新: {app.library_root}"
        return app.library_root

    def set_keep_last(self, value: object) -> Optional[int]:
        """Persist the per-library ``keep_last`` (0 = unlimited).

        Accepts an ``int`` or a base-10 digit string (the settings entry text);
        anything else is rejected with ``None`` and ``settings.json`` is left
        untouched. Changing the setting never prunes existing versions -- pruning
        still happens only when a new version is added.
        """
        app = self.app
        parsed = parse_keep_last(value)
        if parsed is None:
            return None
        if not save_keep_last(app.library_root, parsed):
            return None
        app.status_text = (
            "保留版本数已设为不限制" if parsed == 0 else f"保留版本数已设为 {parsed}"
        )
        return parsed

    @staticmethod
    def library_game_id(entry: SaveEntry) -> Optional[str]:
        """Catalog id stamped on a synthetic library row, else ``None``."""
        extra = getattr(entry, "extra", None) or {}
        game_id = extra.get("library_game_id")
        return game_id or None

    @staticmethod
    def library_identity_key(entry: SaveEntry) -> Optional[str]:
        """ROM identity_key persisted on a catalog row, else ``None``.

        Library rows carry the key recorded at backup time; a legacy row has
        none, so callers fall back to the catalog id and never fabricate a ROM
        identity.
        """
        extra = getattr(entry, "extra", None) or {}
        key = extra.get("identity_key")
        if not key:
            return None
        return str(key).strip() or None

    def game_id(self, entry: SaveEntry) -> str:
        """Catalog/device game key for an entry, preferring the library marker."""
        return self.library_game_id(entry) or game_key(entry)

    def library_entries(self) -> List[SaveEntry]:
        """One row per catalog game (newest first), with statuses pre-computed.

        A library row is by definition already backed up, so its status is
        ``unchanged`` and no hashing (or scanning of a device) is needed.
        """
        app = self.app
        catalog = load_catalog(app.library_root)
        entries = catalog_entries(catalog, app.library_root)
        app._backup_statuses = {}
        for entry in entries:
            game = catalog.games.get(entry.extra.get("library_game_id"))
            app._backup_statuses[entry.path] = self._library_status(game)
        return entries

    def _library_status(self, game: Optional[object]) -> SaveBackupStatus:
        latest = latest_snapshot(game) if game is not None else None
        if latest is None:
            return SaveBackupStatus(status="new")
        return SaveBackupStatus(
            status="unchanged",
            source_mtime=path_mtime_iso(latest.absolute_path(self.app.library_root)),
            last_backup_at=latest.created_at,
            sha256=latest.sha256,
        )

    def set_library_mode(self, enabled: bool) -> bool:
        """Switch the browse source between the device and the local library.

        Switching source clears the platform filter so a stale selection can
        never leave a newly-selected source looking empty.
        """
        app = self.app
        enabled = bool(enabled)
        if enabled != app.library_mode:
            app.library_mode = enabled
            app.selected_platform = "all"
            app._backup_statuses = {}
        self._refresh_source_status()
        return app.library_mode

    def _refresh_source_status(self) -> None:
        app = self.app
        if app.library_mode:
            app.status_text = f"本地存档库 · {len(app.visible_saves())} 款游戏 | [只读]"
        elif app.current_result is not None:
            app.status_text = build_status_text(
                app.current_result, counts=app.backup_status_counts()
            )
        else:
            app.status_text = "就绪"

    def all_saves(self) -> List[SaveEntry]:
        app = self.app
        if app.library_mode:
            return self.library_entries()
        if not app.current_result:
            return []
        return list(app.current_result.saves)

    def platform_counts(self) -> Dict[str, int]:
        counts = {key: 0 for key in PLATFORM_ORDER if key != "all"}
        for save in self.all_saves():
            key = save.platform if save.platform in counts else save.platform
            counts[key] = counts.get(key, 0) + 1
        return counts

    def visible_saves(self) -> List[SaveEntry]:
        app = self.app
        saves = self.all_saves()
        if app.selected_platform not in (None, "", "all"):
            saves = [save for save in saves if save.platform == app.selected_platform]
        query = (app.search_query or "").strip().lower()
        if query:
            saves = [
                save
                for save in saves
                if query in (save.display_name or "").lower()
                or query in (save.title_id or "").lower()
                or query in (save.source_id or "").lower()
            ]
        if app.starred_only:
            catalog = load_catalog(app.library_root)
            saves = [
                save
                for save in saves
                if catalog.games.get(self.game_id(save))
                and catalog.games[self.game_id(save)].starred
            ]
        # Filter combination is unchanged: new + changed stay visible, only
        # unchanged rows are dropped when "仅显示有更新" is active. Every library
        # row is already backed up, so the filter is not applied in library mode
        # (otherwise it would blank the whole browser).
        if app.hide_unchanged and not app.library_mode:
            saves = [save for save in saves if self.save_status(save).status != "unchanged"]
        return saves

    def save_status(self, entry: SaveEntry) -> SaveBackupStatus:
        app = self.app
        cached = app._backup_statuses.get(entry.path)
        if cached is not None:
            return cached
        if self.library_game_id(entry) is not None:
            game = load_catalog(app.library_root).games.get(self.library_game_id(entry))
            status = self._library_status(game)
        else:
            status = classify_save_status(entry, load_catalog(app.library_root))
        app._backup_statuses[entry.path] = status
        return status

    def backup_status_counts(self) -> Dict[str, int]:
        counts = {"new": 0, "changed": 0, "unchanged": 0}
        for save in self.all_saves():
            key = self.save_status(save).status
            if key not in counts:
                counts[key] = 0
            counts[key] += 1
        return counts

    def toggle_hide_unchanged(self) -> bool:
        """Toggle hide_unchanged; returns the new hide_unchanged value."""
        self.app.hide_unchanged = not self.app.hide_unchanged
        return self.app.hide_unchanged

    def _refresh_entry_status(
        self, entry: SaveEntry, catalog: Optional[Catalog] = None
    ) -> SaveBackupStatus:
        app = self.app
        catalog = catalog if catalog is not None else load_catalog(app.library_root)
        hash_error = False
        digest: Optional[str] = None
        try:
            digest = _hash_tree(Path(entry.path))
        except (OSError, ValueError, FileNotFoundError) as e:
            hash_error = True
            app.warnings.append(f"计算存档哈希失败: {entry.display_name or entry.path}: {e}")
        status = classify_save_status(
            entry,
            catalog,
            digest=digest,
            hash_error=hash_error,
        )
        app._backup_statuses[entry.path] = status
        return status

    def set_search_query(self, query: str) -> None:
        self.app.search_query = query or ""

    def toggle_starred_only(self) -> bool:
        self.app.starred_only = not self.app.starred_only
        return self.app.starred_only

    def toggle_star(self, entry: SaveEntry) -> bool:
        app = self.app
        catalog = load_catalog(app.library_root)
        current = catalog.games.get(self.game_id(entry))
        starred = not bool(current and current.starred)
        game = set_game_meta(app.library_root, entry, starred=starred)
        app.status_text = "已加入收藏" if game.starred else "已取消收藏"
        return game.starred

    def is_starred(self, entry: SaveEntry) -> bool:
        game = load_catalog(self.app.library_root).games.get(self.game_id(entry))
        return bool(game and game.starred)

    def game_note(self, entry: SaveEntry) -> str:
        game = load_catalog(self.app.library_root).games.get(self.game_id(entry))
        return game.note if game else ""

    def set_note(self, entry: SaveEntry, note: str) -> None:
        set_game_meta(self.app.library_root, entry, note=note)
        self.app.status_text = "已保存备注"

    def delete_library_game(self, entry: SaveEntry) -> GameDeletion:
        """Delete one game's local snapshots and covers from the library.

        ``entry`` may be a library row or a device save; either way only the
        catalog game it maps to is removed, never the device save itself. The
        caller is responsible for confirming intent first (a cancelled delete
        must never reach this method).
        """
        app = self.app
        game_id = self.game_id(entry)
        result = delete_game(app.library_root, game_id)
        app._backup_statuses = {}
        name = entry.display_name or game_id
        if result.ok:
            app.status_text = f"已删除备份 · {name}"
        elif not result.found:
            app.status_text = "未找到要删除的备份"
        else:
            app.status_text = (
                f"删除未完成 · {name}（保留 {result.snapshots_retained} 个版本）"
            )
        return result

    def collection_stats(self) -> Dict[str, int]:
        return collection_stats(self.app.library_root)

    def export_version_zip(
        self, snapshot: Snapshot, zip_path: Union[Path, str]
    ) -> Optional[Path]:
        app = self.app
        try:
            exported = export_snapshot_zip(snapshot, app.library_root, Path(zip_path))
        except Exception as e:
            app.warnings.append(f"导出失败: {e}")
            app.status_text = f"导出失败: {e}"
            return None
        app.status_text = f"已导出 ZIP: {exported}"
        return exported

    def grouped_saves(self) -> List[Tuple[str, List[SaveEntry]]]:
        app = self.app
        saves = self.visible_saves()
        if not saves:
            return []
        if app.library_mode:
            return [("all", saves)]
        known = [key for key in PLATFORM_ORDER if key != "all"]
        buckets: Dict[str, List[SaveEntry]] = {key: [] for key in known}
        extras: Dict[str, List[SaveEntry]] = {}
        for save in saves:
            if save.platform in buckets:
                buckets[save.platform].append(save)
            else:
                extras.setdefault(save.platform, []).append(save)
        groups: List[Tuple[str, List[SaveEntry]]] = []
        for key in known:
            if buckets[key]:
                groups.append((key, buckets[key]))
        for key, items in extras.items():
            groups.append((key, items))
        return groups

    def set_platform_filter(self, platform: str) -> None:
        app = self.app
        app.selected_platform = platform or "all"
        label = PLATFORM_LABELS.get(app.selected_platform, app.selected_platform)
        count = len(self.visible_saves())
        if app.library_mode:
            app.status_text = f"本地存档库 · {label} · {count} 款游戏 | [只读]"
        elif app.current_result:
            app.status_text = f"{label} · {count} 个存档 | [只读]"

    def _identity_key_for_backup(self, entry: SaveEntry) -> Optional[str]:
        """Best-effort ROM identity key to persist with a backup.

        ``None`` means the resolver could not identify the ROM (unresolved or
        ambiguous); it is forwarded as-is so ``backup_save`` never clobbers an
        identity a previous backup already stored.
        """
        try:
            return self.app.resolve_save_identity(entry).identity_key
        except Exception:  # noqa: BLE001 - identity is best-effort; backup proceeds
            return None

    def import_save(self, entry: SaveEntry) -> Optional[Path]:
        """Backup one save into the versioned local library. Never writes to the source volume."""
        app = self.app
        if (
            self.library_game_id(entry) is not None
            or app.library_mode
            or is_inside_library(entry.path, app.library_root)
        ):
            app.status_text = "本地存档库无需备份"
            return None
        identity_key = self._identity_key_for_backup(entry)
        try:
            result = backup_save(entry, app.library_root, identity_key=identity_key)
        except Exception as e:
            app.warnings.append(f"备份失败: {e}")
            app.status_text = f"备份失败: {e}"
            return None
        app.last_backup = result
        app.last_import_path = result.path
        self._refresh_entry_status(entry)
        if result.is_new:
            app.status_text = f"已保存到本地 · 新版本 {result.snapshot.id}"
        else:
            app.status_text = f"已保存到本地 · 内容未变化，沿用版本 {result.snapshot.id}"
        return result.path

    def import_selected_saves(
        self, entries: List[SaveEntry], token: CancelToken | None = None
    ) -> List[Path]:
        """Backup explicit multi-selection via the cancellable job runner."""
        return run_selected_backups(self.app, entries, token)

    def import_visible_saves(self) -> List[Path]:
        return self.import_selected_saves(list(self.visible_saves()))

    def versions_for_entry(self, entry: SaveEntry) -> List[Snapshot]:
        catalog = load_catalog(self.app.library_root)
        game_id = self.library_game_id(entry)
        if game_id is not None:
            game = catalog.games.get(game_id)
            return list(game.versions) if game is not None else []
        return versions_for(catalog, entry)

    def restore_version(
        self, snapshot: Snapshot, destination: Union[Path, str]
    ) -> Optional[Path]:
        app = self.app
        try:
            restored = restore_snapshot(snapshot, app.library_root, Path(destination))
        except Exception as e:
            app.warnings.append(f"恢复失败: {e}")
            app.status_text = f"恢复失败: {e}"
            return None
        app.status_text = f"已恢复版本 {snapshot.id} 到 {restored}"
        return restored

"""Mount scan and backup-status hashing."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple, Union

from .library import (
    SaveBackupStatus,
    classify_save_status,
    game_key,
    load_catalog,
    path_mtime_iso,
)
from .models import SaveEntry, ScanResult
from .platforms.common import IDLE_SCAN_PROGRESS, ScanProgress

if TYPE_CHECKING:
    from .app_state import AppState


def _hash_tree(path: Path) -> str:
    """Resolve ``hash_tree`` via ``app_state`` so tests can monkeypatch it."""
    from . import app_state

    return app_state.hash_tree(path)


def build_status_text(result: ScanResult, counts: Optional[Dict[str, int]] = None) -> str:
    """Build a human-readable status text for a ScanResult."""
    if not Path(result.root_path).exists():
        base = f"路径不存在或不可读: {result.root_path}"
    else:
        has_encrypted_3ds = any(
            s.source_id == "3ds_sd" or (s.extra and s.extra.get("encrypted_container"))
            for s in result.sources
        )
        if has_encrypted_3ds and not result.saves:
            base = "平台: 3DS | 发现加密 SD 卡 (Nintendo 3DS)，原生存档已加密，不可直接管理 | [只读]"
        elif result.platform == "unknown" and not result.saves:
            base = "平台: 未知/通用卷 | 发现 0 个可识别存档 | [只读]"
        else:
            source_count = len(result.sources)
            save_count = len(result.saves)
            base = f"平台: {result.platform.upper()} | 来源: {source_count} 个 | 存档: {save_count} 个 | [只读]"

    if counts is None:
        return base
    return (
        f"{base} | 新 {counts.get('new', 0)} · "
        f"有变化 {counts.get('changed', 0)} · "
        f"已备份 {counts.get('unchanged', 0)}"
    )


@dataclass(frozen=True)
class PreparedMountScan:
    """后台扫描产生、等待由 UI 主线程原子提交的结果。"""

    mount_point: Path
    result: ScanResult
    backup_statuses: Dict[str, SaveBackupStatus]
    status_warnings: Tuple[str, ...]
    status_counts: Dict[str, int]
    status_text: str


@dataclass(frozen=True)
class PreparedBackupStatuses:
    """第二阶段后台哈希产生、等待由 UI 主线程提交的结果。"""

    statuses: Dict[str, SaveBackupStatus]
    status_warnings: Tuple[str, ...]
    status_counts: Dict[str, int]
    status_text: Optional[str] = None
    entries: Tuple[SaveEntry, ...] = ()
    mount_point: Optional[Path] = None


class ScanSession:
    def __init__(self, app: AppState) -> None:
        self.app = app

    def report_scan_progress(self, progress: ScanProgress) -> None:
        """Record latest scan/hash progress; safe to call from a worker thread."""
        app = self.app
        with app._progress_lock:
            app._scan_progress = progress
            if progress.message:
                app.status_text = progress.message

    def scan_progress(self) -> ScanProgress:
        with self.app._progress_lock:
            return self.app._scan_progress

    def clear_scan_progress(self) -> None:
        with self.app._progress_lock:
            self.app._scan_progress = IDLE_SCAN_PROGRESS

    def _invoke_scan(self, path: Path) -> ScanResult:
        def on_progress(item: ScanProgress) -> None:
            self.report_scan_progress(item)

        try:
            return self.app.scan_fn(path, progress=on_progress)
        except TypeError:
            return self.app.scan_fn(path)

    def begin_mount_scan(self, mount_point: Union[Path, str], auto: bool = False) -> Path:
        """在主线程切换到待扫描设备，并清除上一设备的展示状态。"""
        app = self.app
        path = Path(mount_point)
        if app.library_mode or app.current_mount != path:
            app.selected_platform = "all"
        app.library_mode = False
        app.current_mount = path
        app._auto_selected_mount = auto
        app._identity_resolver = None
        app.current_result = None
        app._backup_statuses = {}
        app.warnings = []
        self.report_scan_progress(ScanProgress(message="正在扫描…", current=0, total=0))
        return path

    def prepare_mount_scan(self, mount_point: Union[Path, str]) -> PreparedMountScan:
        """扫描并计算廉价备份状态（不计算哈希）；可在工作线程运行。"""
        path = Path(mount_point)
        try:
            res = self._invoke_scan(path)
        except Exception as e:
            res = ScanResult(
                root_path=str(path),
                platform="unknown",
                sources=[],
                saves=[],
                warnings=[f"扫描异常: {e}"],
            )
        catalog = load_catalog(self.app.library_root)
        statuses: Dict[str, SaveBackupStatus] = {}
        for entry in res.saves:
            game = catalog.games.get(game_key(entry))
            latest = game.versions[-1] if game and game.versions else None
            if latest is None:
                statuses[entry.path] = SaveBackupStatus(
                    status="new",
                    source_mtime=path_mtime_iso(entry.path),
                    last_backup_at=None,
                    mtime_stale=False,
                    sha256=None,
                )
            else:
                statuses[entry.path] = SaveBackupStatus(
                    status="changed",
                    source_mtime=path_mtime_iso(entry.path),
                    last_backup_at=latest.created_at,
                    mtime_stale=False,
                    sha256=None,
                )
        counts = {"new": 0, "changed": 0, "unchanged": 0}
        for status in statuses.values():
            counts[status.status] = counts.get(status.status, 0) + 1
        return PreparedMountScan(
            mount_point=path,
            result=res,
            backup_statuses=statuses,
            status_warnings=(),
            status_counts=counts,
            status_text=build_status_text(res, counts=counts),
        )

    def apply_prepared_mount_scan(self, prepared: PreparedMountScan) -> ScanResult:
        """在主线程一次性提交后台扫描结果。"""
        app = self.app
        app.current_mount = prepared.mount_point
        app.current_result = prepared.result
        app._identity_resolver = None
        app._backup_statuses = dict(prepared.backup_statuses)
        app.warnings = [*prepared.result.warnings, *prepared.status_warnings]
        app.status_text = prepared.status_text
        return prepared.result

    def _calculate_device_statuses(
        self, entries: List[SaveEntry]
    ) -> Tuple[Dict[str, SaveBackupStatus], List[str], Dict[str, int]]:
        """计算设备存档状态，不修改 AppState，供后台扫描安全调用。"""
        statuses: Dict[str, SaveBackupStatus] = {}
        status_warnings: List[str] = []
        catalog = load_catalog(self.app.library_root)
        pending: List[SaveEntry] = []
        for entry in entries:
            game = catalog.games.get(game_key(entry))
            if not game or not game.versions:
                statuses[entry.path] = classify_save_status(entry, catalog, digest=None)
            else:
                pending.append(entry)
        if pending:
            def _hash_one(item: SaveEntry) -> Tuple[str, Optional[str], Optional[BaseException]]:
                try:
                    return item.path, _hash_tree(Path(item.path)), None
                except (OSError, ValueError, FileNotFoundError) as exc:
                    return item.path, None, exc

            workers = min(2, len(pending))
            self.report_scan_progress(
                ScanProgress(f"正在核对备份 0/{len(pending)}", 0, len(pending))
            )
            hashed: List[Tuple[str, Optional[str], Optional[BaseException]]] = []
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_hash_one, item) for item in pending]
                done = 0
                for future in as_completed(futures):
                    hashed.append(future.result())
                    done += 1
                    self.report_scan_progress(
                        ScanProgress(
                            f"正在核对备份 {done}/{len(pending)}", done, len(pending)
                        )
                    )
            by_path = {path: (digest, err) for path, digest, err in hashed}
            for entry in pending:
                digest, err = by_path[entry.path]
                if err is not None:
                    status_warnings.append(
                        f"计算存档哈希失败: {entry.display_name or entry.path}: {err}"
                    )
                    statuses[entry.path] = classify_save_status(
                        entry, catalog, hash_error=True
                    )
                else:
                    statuses[entry.path] = classify_save_status(
                        entry, catalog, digest=digest
                    )
        counts = {"new": 0, "changed": 0, "unchanged": 0}
        for status in statuses.values():
            counts[status.status] = counts.get(status.status, 0) + 1
        return statuses, status_warnings, counts

    def prepare_backup_statuses(
        self, entries: Optional[Sequence[SaveEntry]] = None
    ) -> PreparedBackupStatuses:
        """计算设备存档真实哈希与状态；可在工作线程运行。最多 2 个工作线程。"""
        app = self.app
        target = (
            list(entries)
            if entries is not None
            else (list(app.current_result.saves) if app.current_result else [])
        )
        statuses, status_warnings, counts = self._calculate_device_statuses(target)
        status_text = None
        if app.current_result is not None:
            status_text = build_status_text(app.current_result, counts=counts)
        return PreparedBackupStatuses(
            statuses=statuses,
            status_warnings=tuple(status_warnings),
            status_counts=counts,
            status_text=status_text,
            entries=tuple(target),
            mount_point=app.current_mount,
        )

    def apply_backup_statuses(self, prepared: PreparedBackupStatuses) -> bool:
        """在主线程提交后台哈希结果。若当前展示的已不是该批存档则丢弃 (no-op)。"""
        app = self.app
        if app.current_result is None:
            return False
        if prepared.mount_point is not None and app.current_mount != prepared.mount_point:
            return False
        if prepared.entries and tuple(app.current_result.saves) != prepared.entries:
            return False
        app._backup_statuses.update(prepared.statuses)
        app.warnings.extend(prepared.status_warnings)
        app.status_text = build_status_text(
            app.current_result, counts=app.backup_status_counts()
        )
        self.clear_scan_progress()
        return True

    def select_mount(self, mount_point: Union[Path, str], auto: bool = False) -> ScanResult:
        """Select a volume or path synchronously and update current result.

        ``auto`` marks a selection the app made on the user's behalf; only those
        may later be replaced by a better device (see ``ensure_mount_selected``).
        """
        path = self.begin_mount_scan(mount_point, auto=auto)
        prepared_scan = self.prepare_mount_scan(path)
        self.apply_prepared_mount_scan(prepared_scan)
        prepared_statuses = self.prepare_backup_statuses(prepared_scan.result.saves)
        self.apply_backup_statuses(prepared_statuses)
        return prepared_scan.result

    def refresh_backup_statuses(self) -> Dict[str, int]:
        """Recompute and cache status for every scanned save."""
        app = self.app
        if app.library_mode:
            app.library_entries()
            return app.backup_status_counts()
        entries = app.all_saves()
        prepared = self.prepare_backup_statuses(entries)
        self.apply_backup_statuses(prepared)
        return prepared.status_counts

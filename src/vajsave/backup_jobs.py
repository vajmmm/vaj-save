"""Sequential, cancellable backups of SaveEntry lists."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .jobs import CancelToken, JobCancelled, JobProgress
from .models import SaveEntry

if TYPE_CHECKING:
    from .app_state import AppState

BackupOne = Callable[[SaveEntry], Optional[Path]]
ProgressCb = Callable[[JobProgress], None]

UPDATED_STATUSES = frozenset({"new", "changed"})


def backup_entries(
    entries: Sequence[SaveEntry],
    backup_one: BackupOne,
    token: CancelToken | None = None,
    progress: ProgressCb | None = None,
) -> list[Path]:
    """Backup ``entries`` in order. Cancel is checked between entries.

    Already-returned paths are kept. ``JobCancelled`` is swallowed so callers
    receive the partial list.
    """
    copied: list[Path] = []
    total = len(entries)

    def report(*, message: str, current: int, cancellable: bool) -> None:
        if progress is None:
            return
        progress(
            JobProgress(
                message=message,
                current=current,
                total=total,
                cancellable=cancellable,
            )
        )

    try:
        for index, entry in enumerate(entries):
            if token is not None:
                token.raise_if_cancelled()
            name = entry.display_name or entry.title_id or entry.path
            report(
                message=f"正在备份 {index + 1}/{total} · {name}",
                current=index,
                cancellable=token is not None,
            )
            dest = backup_one(entry)
            if dest is not None:
                copied.append(Path(dest))
    except JobCancelled:
        report(
            message=f"已备份 {len(copied)} 个，已取消",
            current=len(copied),
            cancellable=False,
        )
        return copied

    report(message="", current=total, cancellable=False)
    return copied


def updated_visible_saves(app: AppState) -> list[SaveEntry]:
    return [
        entry
        for entry in app.visible_saves()
        if app.save_status(entry).status in UPDATED_STATUSES
    ]


def run_selected_backups(
    app: AppState,
    entries: Sequence[SaveEntry],
    token: CancelToken | None = None,
) -> list[Path]:
    started = app._try_begin_job(token)
    if started is None:
        return []
    new_count = 0

    def backup_one(entry: SaveEntry) -> Path | None:
        nonlocal new_count
        dest = app.library_actions.import_save(entry)
        if dest is not None and app.last_backup and app.last_backup.is_new:
            new_count += 1
        return dest

    try:
        copied = backup_entries(
            list(entries),
            backup_one,
            token=started,
            progress=app._report_job_progress,
        )
    finally:
        app._end_job(started)

    if started.cancelled():
        app.status_text = f"已备份 {len(copied)} 个，已取消"
    elif copied:
        app.status_text = (
            f"已备份 {len(copied)} 个存档到本地库（新增 {new_count} 个版本）"
        )
    elif not entries:
        app.status_text = "当前没有可备份的存档"
    return copied


def backup_updated_saves(
    app: AppState,
    token: CancelToken | None = None,
) -> list[Path]:
    if app.library_mode:
        app.status_text = "本地存档库无需备份"
        return []
    targets = updated_visible_saves(app)
    if not targets:
        app.status_text = "当前没有可备份的存档"
        return []
    return run_selected_backups(app, targets, token)

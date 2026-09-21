from datetime import datetime
from pathlib import Path

from conftest import build_sfo
from vajsave.app_state import AppState
from vajsave.backup_jobs import backup_entries
from vajsave.jobs import CancelToken, JobCancelled, JobProgress
from vajsave.library import backup_save, load_catalog
from vajsave.models import SaveEntry


def _entry(name: str) -> SaveEntry:
    return SaveEntry(
        platform="psp",
        source_id="psp",
        display_name=name,
        path=f"/virtual/{name}",
        title_id=name,
    )


def _psp_save(root: Path, title_id: str, payload: bytes, title: str | None = None) -> Path:
    save_dir = root / "PSP" / "SAVEDATA" / title_id
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(
        build_sfo(
            {
                "TITLE": title or title_id,
                "TITLE_ID": title_id,
                "CATEGORY": "MS",
                "SAVEDATA_DIRECTORY": title_id,
            }
        )
    )
    (save_dir / "DATA.BIN").write_bytes(payload)
    return save_dir


def _psp_entry(save_dir: Path, title_id: str, title: str | None = None) -> SaveEntry:
    return SaveEntry(
        platform="psp",
        source_id="psp",
        display_name=title or title_id,
        path=str(save_dir),
        title_id=title_id,
    )


def test_cancel_token_raise_if_cancelled():
    token = CancelToken()
    assert token.cancelled() is False
    token.raise_if_cancelled()
    token.cancel()
    assert token.cancelled() is True
    try:
        token.raise_if_cancelled()
    except JobCancelled:
        return
    raise AssertionError("expected JobCancelled")


def test_backup_entries_skips_after_cancel(tmp_path: Path):
    e1, e2, e3 = _entry("one"), _entry("two"), _entry("three")
    token = CancelToken()
    calls: list[str] = []

    def backup_one(entry: SaveEntry) -> Path:
        calls.append(entry.display_name)
        if len(calls) == 1:
            token.cancel()
        return tmp_path / entry.display_name

    paths = backup_entries([e1, e2, e3], backup_one, token=token)
    assert calls == ["one"]
    assert [p.name for p in paths] == ["one"]
    assert len(paths) == 1


def test_backup_entries_reports_cancellable_progress(tmp_path: Path):
    entries = [_entry("one"), _entry("two")]
    snapshots: list[JobProgress] = []

    def backup_one(entry: SaveEntry) -> Path:
        return tmp_path / entry.display_name

    paths = backup_entries(
        entries, backup_one, token=CancelToken(), progress=snapshots.append
    )
    assert len(paths) == 2
    assert snapshots
    assert snapshots[0].cancellable is True
    assert snapshots[0].total == 2
    assert snapshots[0].current == 0
    assert snapshots[-1].cancellable is False
    assert snapshots[-1].current == 2


def test_backup_updated_saves_ignores_unchanged(tmp_path: Path):
    root = tmp_path / "PSP_VOL"
    save_new = _psp_save(root, "ULUS00001", b"new-bytes", "New Game")
    save_same = _psp_save(root, "ULUS00002", b"same-bytes", "Same Game")
    save_changed = _psp_save(root, "ULUS00003", b"old-bytes", "Changed Game")
    lib = tmp_path / "lib"

    backup_save(_psp_entry(save_same, "ULUS00002", "Same Game"), lib, datetime(2026, 1, 1, 10, 0, 0))
    backup_save(
        _psp_entry(save_changed, "ULUS00003", "Changed Game"), lib, datetime(2026, 1, 1, 10, 0, 0)
    )
    (save_changed / "DATA.BIN").write_bytes(b"changed-bytes")

    state = AppState(library_root=lib)
    state.select_mount(root)
    by_id = {save.title_id: save for save in state.all_saves()}
    assert set(by_id) >= {"ULUS00001", "ULUS00002", "ULUS00003"}
    assert state.save_status(by_id["ULUS00001"]).status == "new"
    assert state.save_status(by_id["ULUS00002"]).status == "unchanged"
    assert state.save_status(by_id["ULUS00003"]).status == "changed"

    catalog_before = load_catalog(lib)
    unchanged_game = next(
        game for game in catalog_before.games.values() if game.title_id == "ULUS00002"
    )
    unchanged_versions = len(unchanged_game.versions)

    copied = state.backup_updated_saves()
    assert len(copied) == 2
    copied_ids = {path.parent.name if path.is_file() else path.name for path in copied}
    assert "ULUS00002" not in copied_ids

    assert state.save_status(by_id["ULUS00001"]).status == "unchanged"
    assert state.save_status(by_id["ULUS00002"]).status == "unchanged"
    assert state.save_status(by_id["ULUS00003"]).status == "unchanged"
    catalog_after = load_catalog(lib)
    same_game = next(game for game in catalog_after.games.values() if game.title_id == "ULUS00002")
    assert len(same_game.versions) == unchanged_versions
    new_game = next(game for game in catalog_after.games.values() if game.title_id == "ULUS00001")
    changed_game = next(game for game in catalog_after.games.values() if game.title_id == "ULUS00003")
    assert new_game.versions
    assert len(changed_game.versions) == 2


def test_backup_updated_saves_noop_in_library_mode(tmp_path: Path):
    lib = tmp_path / "lib"
    src = tmp_path / "src" / "ULJM05800"
    src.mkdir(parents=True)
    (src / "DATA.BIN").write_bytes(b"library")
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="Library Game",
        path=str(src),
        title_id="ULJM05800",
    )
    backup_save(entry, lib, datetime(2026, 1, 1, 10, 0, 0))

    state = AppState(library_root=lib)
    state.set_library_mode(True)
    assert state.visible_saves()
    before = sorted(p.relative_to(lib) for p in lib.rglob("*") if p.is_file())

    copied = state.backup_updated_saves()
    assert copied == []
    after = sorted(p.relative_to(lib) for p in lib.rglob("*") if p.is_file())
    assert after == before
    assert "本地存档库" in state.status_text


def test_import_selected_cancel_keeps_written_versions(tmp_path: Path, monkeypatch):
    root = tmp_path / "PSP_VOL"
    _psp_save(root, "ULUS00001", b"one", "One")
    _psp_save(root, "ULUS00002", b"two", "Two")
    _psp_save(root, "ULUS00003", b"three", "Three")
    lib = tmp_path / "lib"
    state = AppState(library_root=lib)
    state.select_mount(root)
    entries = list(state.visible_saves())
    assert len(entries) == 3

    token = CancelToken()
    real = state.library_actions.import_save

    def backup_one(entry: SaveEntry):
        dest = real(entry)
        token.cancel()
        return dest

    monkeypatch.setattr(state.library_actions, "import_save", backup_one)
    copied = state.import_selected_saves(entries, token=token)
    assert len(copied) == 1
    assert "已备份 1 个，已取消" in state.status_text

    catalog = load_catalog(lib)
    assert len(catalog.games) == 1
    game = next(iter(catalog.games.values()))
    assert game.versions


def test_backup_updated_saves_noops_while_job_running(tmp_path: Path, monkeypatch):
    root = tmp_path / "PSP_VOL"
    _psp_save(root, "ULUS00001", b"one", "One")
    _psp_save(root, "ULUS00002", b"two", "Two")
    lib = tmp_path / "lib"
    state = AppState(library_root=lib)
    state.select_mount(root)

    nested: list[list[Path]] = []
    real = state.library_actions.import_save

    def backup_one(entry: SaveEntry):
        nested.append(list(state.backup_updated_saves()))
        return real(entry)

    monkeypatch.setattr(state.library_actions, "import_save", backup_one)
    copied = state.backup_updated_saves()
    assert len(copied) == 2
    assert nested
    assert nested[0] == []
    assert "已有任务" in state.status_text or "已备份 2" in state.status_text


def test_job_progress_cancellable_during_and_idle_after(tmp_path: Path, monkeypatch):
    root = tmp_path / "PSP_VOL"
    _psp_save(root, "ULUS00001", b"one", "One")
    lib = tmp_path / "lib"
    state = AppState(library_root=lib)
    state.select_mount(root)

    seen: list[JobProgress] = []
    real = state.library_actions.import_save

    def backup_one(entry: SaveEntry):
        seen.append(state.job_progress())
        return real(entry)

    monkeypatch.setattr(state.library_actions, "import_save", backup_one)
    copied = state.backup_updated_saves()
    assert copied
    assert seen
    assert seen[0].cancellable is True
    assert seen[0].total == 1
    idle = state.job_progress()
    assert idle.cancellable is False
    assert idle.message == ""


def test_cancel_job_with_no_job_is_safe():
    state = AppState()
    state.cancel_job()
    progress = state.job_progress()
    assert progress.cancellable is False
    assert progress.current == 0
    assert progress.total == 0

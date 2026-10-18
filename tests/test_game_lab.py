from datetime import datetime
from pathlib import Path

from vajsave.app_state import AppState
from vajsave.library import backup_save, export_snapshot_zip, load_catalog
from vajsave.models import SaveEntry, VolumeInfo
from vajsave.volume import FakeVolumeProvider
from conftest import build_sfo


def _psp_entry(tmp_path: Path, psp_sfo_bytes: bytes) -> tuple[AppState, SaveEntry]:
    root = tmp_path / "PSP_VOL"
    save_dir = root / "PSP" / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    (save_dir / "DATA.BIN").write_bytes(b"DATA")
    state = AppState(
        provider=FakeVolumeProvider([VolumeInfo(name="PSP", mount_point=root)]),
        library_root=tmp_path / "lib",
    )
    state.select_mount(root)
    return state, state.visible_saves()[0]


def test_search_filters_visible_saves(tmp_path: Path, psp_sfo_bytes: bytes):
    state, _ = _psp_entry(tmp_path, psp_sfo_bytes)
    state.set_search_query("monster")
    assert len(state.visible_saves()) == 1
    state.set_search_query("zzz-not-found")
    assert state.visible_saves() == []


def test_star_and_starred_only_filter(tmp_path: Path, psp_sfo_bytes: bytes):
    state, entry = _psp_entry(tmp_path, psp_sfo_bytes)
    assert state.is_starred(entry) is False
    assert state.toggle_star(entry) is True
    assert state.is_starred(entry) is True
    state.starred_only = True
    assert len(state.visible_saves()) == 1
    state.toggle_star(entry)
    assert state.visible_saves() == []


def test_cleared_mark_persists_and_filters(tmp_path: Path, psp_sfo_bytes: bytes):
    state, entry = _psp_entry(tmp_path, psp_sfo_bytes)
    assert state.is_cleared(entry) is False
    assert state.toggle_cleared(entry) is True
    assert state.status_text == "已标记通关"
    assert state.is_cleared(entry) is True

    state.toggle_star(entry)
    state.set_note(entry, "一周目")
    game = load_catalog(state.library_root).games[state._game_id(entry)]
    assert game.cleared is True
    assert game.starred is True
    assert game.note == "一周目"
    assert state.collection_stats()["cleared"] == 1

    state.toggle_cleared_only()
    assert [save.path for save in state.visible_saves()] == [entry.path]
    state.toggle_cleared(entry)
    assert state.visible_saves() == []
    assert state.status_text == "已取消通关"
    state.toggle_cleared(entry)

    reloaded = AppState(provider=FakeVolumeProvider([]), library_root=state.library_root)
    reloaded.current_result = state.current_result
    reloaded.current_mount = state.current_mount
    assert reloaded.is_cleared(entry) is True
    assert reloaded.is_starred(entry) is True
    assert reloaded.game_note(entry) == "一周目"


def test_note_persists(tmp_path: Path, psp_sfo_bytes: bytes):
    state, entry = _psp_entry(tmp_path, psp_sfo_bytes)
    state.set_note(entry, "二周目 全图鉴")
    assert state.game_note(entry) == "二周目 全图鉴"
    assert load_catalog(state.library_root).games


def test_export_snapshot_zip(tmp_path: Path):
    src = tmp_path / "ULUS11111"
    src.mkdir()
    (src / "DATA.BIN").write_bytes(b"zip-me")
    entry = SaveEntry(platform="psp", source_id="psp", display_name="Demo", path=str(src), title_id="ULUS11111")
    lib = tmp_path / "lib"
    result = backup_save(entry, lib, datetime(2026, 1, 1, 8, 0, 0))
    zipped = export_snapshot_zip(result.snapshot, lib, tmp_path / "demo.zip")
    assert zipped.is_file()
    assert zipped.stat().st_size > 0

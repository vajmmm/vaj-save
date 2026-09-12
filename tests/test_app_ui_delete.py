"""UI wiring for deleting a local-library backup.

The inspector's primary action becomes a neutral 「删除备份」 while the user is
browsing the local library (it stays the blue 「备份存档」 on a device). Deleting
requires a confirmation, removes the game's snapshots + covers, and a cancelled
confirmation must leave the library untouched. Double-clicking a row never
deletes anything.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from vajsave import app_ui
from vajsave.app_ui import build_app
from vajsave.app_state import AppState
from vajsave.library import backup_save, game_key, load_catalog
from vajsave.models import SaveEntry
from vajsave.volume import FakeVolumeProvider


@pytest.fixture(scope="module")
def tk_root():
    try:
        import tkinter as tk

        root = tk.Tk()
    except Exception:
        pytest.skip("Tkinter display not available")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


def _dispose(app, root) -> None:
    try:
        app._stop_background()
    except Exception:
        pass
    for child in list(root.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass


def _library_game(tmp_path: Path, lib: Path, platform: str, title_id: str, name: str):
    folder = tmp_path / "src" / platform / title_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "save.bin").write_bytes((title_id + name).encode("utf-8"))
    entry = SaveEntry(
        platform=platform,
        source_id=platform,
        display_name=name,
        path=str(folder),
        title_id=title_id,
    )
    backup_save(entry, lib, when=datetime(2024, 1, 1, 10, 0, 0))
    return entry


def _app_with_library(tk_root, tmp_path, *, library: bool = True):
    lib = tmp_path / "lib"
    entry = _library_game(tmp_path, lib, "psp", "ULJM05800", "PSP Game")
    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    app = build_app(state=state, root=tk_root)
    if library:
        state.set_library_mode(True)
        app.refresh_saves_ui()
    return app, state, entry


def _snapshot_dirs(lib: Path, entry: SaveEntry):
    game = load_catalog(lib).games[game_key(entry)]
    return [lib / snap.path for snap in game.versions]


def test_primary_action_is_neutral_delete_in_library_mode(tk_root, tmp_path):
    app, state, _entry = _app_with_library(tk_root, tmp_path, library=False)
    try:
        button = app._action_buttons[0]
        assert button._text == "备份存档"
        assert button.accent is True

        state.set_library_mode(True)
        app.refresh_saves_ui()
        assert app.state.library_mode is True
        assert button._text == "删除备份"
        assert button.accent is False

        state.set_library_mode(False)
        app.refresh_saves_ui()
        assert button._text == "备份存档"
        assert button.accent is True
    finally:
        _dispose(app, tk_root)


def test_cancelled_confirmation_is_zero_side_effect(tk_root, tmp_path, monkeypatch):
    app, state, entry = _app_with_library(tk_root, tmp_path)
    try:
        lib = state.library_root
        before = sorted(str(p) for p in lib.rglob("*") if p.is_file())
        called = []
        monkeypatch.setattr(
            app_ui.messagebox, "askyesno", lambda *a, **k: called.append(True) or False
        )

        app.save_list.selection_set(0)
        app.on_primary_clicked()

        assert called == [True]
        after = sorted(str(p) for p in lib.rglob("*") if p.is_file())
        assert after == before
        assert game_key(entry) in load_catalog(lib).games
    finally:
        _dispose(app, tk_root)


def test_confirmed_delete_removes_game_and_refreshes(tk_root, tmp_path, monkeypatch):
    app, state, entry = _app_with_library(tk_root, tmp_path)
    try:
        lib = state.library_root
        dirs = _snapshot_dirs(lib, entry)
        monkeypatch.setattr(app_ui.messagebox, "askyesno", lambda *a, **k: True)

        app.save_list.selection_set(0)
        app.on_primary_clicked()

        assert game_key(entry) not in load_catalog(lib).games
        for directory in dirs:
            assert not directory.exists()
        assert app.save_list.size() == 0
        assert "已删除" in app.status_label_var.get()
    finally:
        _dispose(app, tk_root)


def test_double_click_in_library_mode_never_deletes(tk_root, tmp_path, monkeypatch):
    app, state, entry = _app_with_library(tk_root, tmp_path)
    try:
        lib = state.library_root

        def _boom(*args, **kwargs):
            raise AssertionError("double-click must not open the delete confirmation")

        monkeypatch.setattr(app_ui.messagebox, "askyesno", _boom)

        app.save_list.activate_index(0)

        assert game_key(entry) in load_catalog(lib).games
        for directory in _snapshot_dirs(lib, entry):
            assert directory.exists()
    finally:
        _dispose(app, tk_root)


def test_partial_delete_is_not_reported_as_success(tk_root, tmp_path, monkeypatch):
    import vajsave.library as library_module

    app, state, entry = _app_with_library(tk_root, tmp_path)
    try:
        monkeypatch.setattr(app_ui.messagebox, "askyesno", lambda *a, **k: True)
        monkeypatch.setattr(
            library_module, "_delete_snapshot_payload", lambda snap, root: False
        )

        app.save_list.selection_set(0)
        app.on_primary_clicked()

        assert "部分" in app.warning_label_var.get()
        # The game could not be removed, so it is still listed.
        assert game_key(entry) in load_catalog(state.library_root).games
    finally:
        _dispose(app, tk_root)


def test_delete_without_selection_warns(tk_root, tmp_path, monkeypatch):
    app, state, entry = _app_with_library(tk_root, tmp_path)
    try:
        called = []
        monkeypatch.setattr(
            app_ui.messagebox, "askyesno", lambda *a, **k: called.append(True) or True
        )
        app.save_list.selection_clear()

        app.on_primary_clicked()

        assert called == []
        assert "先" in app.warning_label_var.get()
        assert game_key(entry) in load_catalog(state.library_root).games
    finally:
        _dispose(app, tk_root)

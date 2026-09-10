"""Behavioral tests for the Switch-style Canvas save tile grid.

The pure geometry helpers live in ``tests/test_ui_theme.py``; here we exercise
selection semantics (click / ctrl-click / shift-click / arrow keys / double
click), scrolling into view, and the wiring into ``VajSaveApp``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vajsave.app_ui import SaveTileGrid, build_app
from vajsave.app_state import AppState
from vajsave.models import SaveEntry, VolumeInfo
from vajsave.ui_theme import SWITCH, tile_face
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


def _faces(n: int = 5):
    faces = []
    for i in range(n):
        entry = SaveEntry(
            platform="psp",
            source_id="psp",
            display_name=f"Game {i}",
            path=f"/tmp/vol/PSP/SAVEDATA/ULJM{i:05d}",
            title_id=f"ULJM{i:05d}",
        )
        status = "new" if i % 2 == 0 else "changed"
        faces.append(tile_face(entry, status))
    return faces


def _grid(root, faces=None, on_select=None, on_activate=None) -> SaveTileGrid:
    grid = SaveTileGrid(root, on_select=on_select, on_activate=on_activate, width=520, height=400)
    grid.pack()
    root.update_idletasks()
    grid.set_tiles(faces if faces is not None else _faces())
    root.update_idletasks()
    return grid


# --- grid model -------------------------------------------------------------


def test_set_tiles_size_matches_faces(tk_root):
    grid = _grid(tk_root)
    try:
        assert grid.size() == 5
        assert grid.curselection() == ()
    finally:
        grid.destroy()


def test_single_click_selects_only_one(tk_root):
    events = []
    grid = _grid(tk_root, on_select=events.append)
    try:
        grid.select_index(1)
        assert grid.curselection() == (1,)
        grid.select_index(3)
        assert grid.curselection() == (3,)
        assert events == [1, 3]
    finally:
        grid.destroy()


def test_ctrl_click_toggles_membership(tk_root):
    grid = _grid(tk_root)
    try:
        grid.select_index(0)
        grid.select_index(2, toggle=True)
        assert grid.curselection() == (0, 2)
        grid.select_index(0, toggle=True)
        assert grid.curselection() == (2,)
    finally:
        grid.destroy()


def test_shift_click_selects_range(tk_root):
    grid = _grid(tk_root)
    try:
        grid.select_index(1)
        grid.select_index(4, extend=True)
        assert grid.curselection() == (1, 2, 3, 4)
        # Extending backwards from the same anchor collapses to the new range.
        grid.select_index(0, extend=True)
        assert grid.curselection() == (0, 1)
    finally:
        grid.destroy()


def test_arrow_keys_move_active_selection(tk_root):
    grid = _grid(tk_root)
    try:
        cols = grid.columns()
        assert cols >= 1
        grid.select_index(0)
        grid.move_active(1, 0)
        assert grid.curselection() == (1,)
        grid.move_active(0, 1)
        assert grid.curselection() == (min(1 + cols, grid.size() - 1),)
        # Clamp at both edges instead of wrapping.
        grid.select_index(0)
        grid.move_active(-1, 0)
        assert grid.curselection() == (0,)
        grid.select_index(grid.size() - 1)
        grid.move_active(1, 0)
        assert grid.curselection() == (grid.size() - 1,)
    finally:
        grid.destroy()


def test_arrow_without_selection_picks_first(tk_root):
    grid = _grid(tk_root, faces=_faces(3))
    try:
        grid.move_active(1, 0)
        assert grid.curselection() == (0,)
    finally:
        grid.destroy()


def test_double_click_activates_and_selects(tk_root):
    activated = []
    grid = _grid(tk_root, on_activate=activated.append)
    try:
        grid.activate_index(2)
        assert activated == [2]
        assert grid.curselection() == (2,)
    finally:
        grid.destroy()


def test_selection_scrolls_into_view(tk_root, monkeypatch):
    grid = _grid(tk_root, faces=_faces(40))
    moves = []
    monkeypatch.setattr(grid, "yview_moveto", lambda frac: moves.append(frac))
    try:
        grid.select_index(0)
        assert moves == []  # first tile is already visible
        grid.select_index(39)
        assert moves and moves[-1] > 0
    finally:
        grid.destroy()


def test_selection_clear_empties_curselection(tk_root):
    grid = _grid(tk_root)
    try:
        grid.select_index(2)
        grid.selection_clear()
        assert grid.curselection() == ()
    finally:
        grid.destroy()


# --- app wiring -------------------------------------------------------------


def _psp_volume(tmp_path: Path, psp_sfo_bytes: bytes, title_ids) -> Path:
    vol = tmp_path / "VOL"
    for title_id in title_ids:
        save_dir = vol / "PSP" / "SAVEDATA" / title_id
        save_dir.mkdir(parents=True)
        (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
        (save_dir / "DATA.BIN").write_bytes(title_id.encode())
    return vol


def test_app_grid_matches_visible_saves_and_wires_selection(tk_root, tmp_path, psp_sfo_bytes):
    import tkinter as tk

    vol = _psp_volume(tmp_path, psp_sfo_bytes, ["ULJM05800", "ULJM05801", "ULJM05802"])
    state = AppState(
        provider=FakeVolumeProvider([VolumeInfo(name="PSP", mount_point=vol)]),
        library_root=tmp_path / "lib",
    )
    state.refresh_volumes()
    app = build_app(state=state, root=tk_root)
    try:
        state.select_mount(vol)
        app.refresh_saves_ui()
        assert app.save_list.size() == len(state.visible_saves()) == 3
        assert len(app._saves_index) == 3

        # The old Listbox contracts stay intact next to the new tile grid.
        assert isinstance(app.vol_list, tk.Listbox)
        assert len(app._volumes_index) == 1
        assert isinstance(app.version_list, tk.Listbox)
        assert app.version_list.bind("<MouseWheel>") == ""
        assert app.version_list.bind("<Button-4>") == ""
        assert "_on_detail_mousewheel" in app.detail_inner.bind("<MouseWheel>")
        assert str(app.actions_frame.pack_info().get("side")) == "bottom"

        app.save_list.select_index(1)
        assert app._selected_save is app._saves_index[1]
        assert app.path_entry_var.get() == app._saves_index[1].path
    finally:
        _dispose(app, tk_root)


def test_app_uses_switch_basic_white_surfaces(tk_root, tmp_path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        assert app.root.cget("bg") == SWITCH["bg"]
        assert app.actions_frame.cget("bg") == SWITCH["surface"]
        assert app.detail_canvas.cget("bg") == SWITCH["surface"]
        assert app.save_list.cget("bg") == SWITCH["bg"]
    finally:
        _dispose(app, tk_root)


def test_app_tile_double_click_backs_up_selected_save(tk_root, tmp_path, psp_sfo_bytes):
    vol = _psp_volume(tmp_path, psp_sfo_bytes, ["ULJM05800"])
    lib = tmp_path / "lib"
    state = AppState(
        provider=FakeVolumeProvider([VolumeInfo(name="PSP", mount_point=vol)]),
        library_root=lib,
    )
    state.refresh_volumes()
    app = build_app(state=state, root=tk_root)
    try:
        state.select_mount(vol)
        app.refresh_saves_ui()
        assert app.save_list.size() == 1
        app.save_list.activate_index(0)
        # Double-click runs the primary "备份" action for the clicked save.
        assert state.last_backup is not None
        assert state.last_backup.snapshot is not None
    finally:
        _dispose(app, tk_root)

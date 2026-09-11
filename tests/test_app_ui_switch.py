"""Behavioral tests for the Switch-style Canvas save tile grid.

The pure geometry helpers live in ``tests/test_ui_theme.py``; here we exercise
selection semantics (click / ctrl-click / shift-click / arrow keys / double
click), scrolling into view, and the wiring into ``VajSaveApp``.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from vajsave import app_ui
from vajsave import ui_theme
from vajsave.app_ui import CanvasButton, SaveTileGrid, build_app
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


def _map_root(root) -> None:
    """Tk only delivers ``event_generate`` events to viewable widgets."""
    root.deiconify()
    root.update()


def _unmap_root(root) -> None:
    root.withdraw()
    root.update()


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


def test_empty_grid_renders_hint_without_error(tk_root):
    grid = _grid(tk_root, faces=[])
    try:
        assert grid.size() == 0
        assert grid.curselection() == ()
        # The hover/selection paths must stay safe on an empty grid.
        grid.set_hover(3)
        assert grid.hover_index is None
        grid.select_index(0)
        assert grid.curselection() == ()
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


# --- CanvasButton -----------------------------------------------------------


def test_ttk_button_and_checkbutton_absent_from_source():
    source = Path(inspect.getsourcefile(app_ui)).read_text(encoding="utf-8")
    assert source.count("ttk.Button") == 0
    assert source.count("ttk.Checkbutton") == 0


def test_canvas_button_states_and_api(tk_root):
    calls = []
    button = CanvasButton(tk_root, text="备份", command=lambda: calls.append(1))
    button.pack()
    tk_root.update_idletasks()
    try:
        assert button.state == "idle"
        assert button.selected is False
        assert button.accent is False
        button.invoke()
        assert calls == [1]

        button.set_text("备份当前列表")
        assert button._text == "备份当前列表"

        button.set_selected(True)
        assert button.selected is True
        assert button.state == "selected"

        accent = CanvasButton(tk_root, text="备份", variant="accent")
        assert accent.accent is True
        assert accent.state == "accent"
        accent.destroy()

        _map_root(tk_root)
        # ``event_generate`` is dispatched synchronously on a mapped widget, so
        # asserting right away keeps real pointer motion from interfering.
        button.event_generate("<Enter>")
        assert button.state == "hover"
        button.event_generate("<ButtonPress-1>")
        assert button.state == "pressed"
        button.event_generate("<ButtonRelease-1>", x=2, y=2)
        assert button.state == "hover"
        button.event_generate("<Leave>")
        assert button.state == "selected"
        button.set_selected(False)
        assert button.state == "idle"
        _unmap_root(tk_root)
    finally:
        button.destroy()


def test_canvas_button_heights_and_measured_width(tk_root):
    bottom = CanvasButton(tk_root, text="刷新", height=CanvasButton.BOTTOM_HEIGHT)
    action = CanvasButton(tk_root, text="备份", height=CanvasButton.ACTION_HEIGHT)
    wide = CanvasButton(tk_root, text="备份当前列表", height=CanvasButton.ACTION_HEIGHT)
    try:
        assert bottom.winfo_reqheight() == 34
        assert action.winfo_reqheight() == 36
        assert CanvasButton.BOTTOM_HEIGHT == 34
        assert CanvasButton.ACTION_HEIGHT == 36
        # Width follows the measured label, not a fixed constant.
        assert wide.winfo_reqwidth() > action.winfo_reqwidth()
    finally:
        bottom.destroy()
        action.destroy()
        wide.destroy()


def test_canvas_button_optional_dot(tk_root):
    plain = CanvasButton(tk_root, text="设置")
    dotted = CanvasButton(tk_root, text="监听插拔", dot=SWITCH["accent"])
    try:
        assert plain.find_withtag("button-dot") == ()
        assert dotted.find_withtag("button-dot") != ()
    finally:
        plain.destroy()
        dotted.destroy()


# --- tile hover / selection --------------------------------------------------


def _tile_center(grid: SaveTileGrid, index: int):
    x1, y1, x2, y2 = grid._boxes[index]
    return int((x1 + x2) / 2), int((y1 + y2) / 2)


def test_tile_hover_redraws_with_hover_token(tk_root):
    grid = _grid(tk_root)
    try:
        _map_root(tk_root)
        cx, cy = _tile_center(grid, 0)
        grid.event_generate("<Enter>", x=cx, y=cy)
        assert grid.hover_index == 0
        fills = [grid.itemcget(i, "fill") for i in grid.find_all()]
        assert SWITCH["hover"] in fills

        grid.event_generate("<Leave>")
        assert grid.hover_index is None
        fills = [grid.itemcget(i, "fill") for i in grid.find_all()]
        assert SWITCH["hover"] not in fills
        _unmap_root(tk_root)
    finally:
        grid.destroy()


def test_tile_click_selects_and_draws_ring(tk_root):
    grid = _grid(tk_root)
    try:
        _map_root(tk_root)
        cx, cy = _tile_center(grid, 1)
        grid.event_generate("<Button-1>", x=cx, y=cy)
        assert grid.curselection() == (1,)
        rings = grid.find_withtag("tile-ring")
        assert rings
        assert grid.itemcget(rings[0], "outline") == SWITCH["ring"]
        assert float(grid.itemcget(rings[0], "width")) == 3
        title = grid.find_withtag("title1")[0]
        assert "bold" in grid.itemcget(title, "font")
        _unmap_root(tk_root)
    finally:
        grid.destroy()


def test_set_hover_ignores_out_of_range(tk_root):
    grid = _grid(tk_root)
    try:
        grid.set_hover(99)
        assert grid.hover_index is None
        grid.set_hover(-1)
        assert grid.hover_index is None
        grid.set_hover(2)
        assert grid.hover_index == 2
    finally:
        grid.destroy()


def test_selected_tile_is_drawn_larger(tk_root):
    grid = _grid(tk_root)
    try:
        before = grid.coords(grid.find_withtag("tile0")[0])
        grid.select_index(0)
        tk_root.update()
        after = grid.coords(grid.find_withtag("tile0")[0])
        before_w = max(before[0::2]) - min(before[0::2])
        after_w = max(after[0::2]) - min(after[0::2])
        assert after_w > before_w
    finally:
        grid.destroy()


# --- app structure -----------------------------------------------------------


def test_refresh_platform_ui_reuses_rows(tk_root, tmp_path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        app.refresh_platform_ui()
        first = {key: row["row"].winfo_id() for key, row in app._platform_rows.items()}
        app.refresh_platform_ui()
        second = {key: row["row"].winfo_id() for key, row in app._platform_rows.items()}
        assert first == second
        assert set(app._platform_rows["all"].keys()) >= {"row", "name", "badge", "pip"}
    finally:
        _dispose(app, tk_root)


def test_toggle_buttons_invoke_and_sync_selected(tk_root, tmp_path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        hide_before = state.hide_unchanged
        app._hide_unchanged_button.invoke()
        assert state.hide_unchanged != hide_before
        assert app._hide_unchanged_button.selected == state.hide_unchanged

        watch_before = app._watch_var.get()
        app._watch_button.invoke()
        assert app._watch_var.get() != watch_before
        assert app._watch_button.selected == app._watch_var.get()
    finally:
        _dispose(app, tk_root)


def test_bottom_bar_fits_at_min_width(tk_root, tmp_path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        tk_root.update_idletasks()
        buttons = getattr(app, "_bottom_buttons", None)
        assert buttons
        # padx pairs used when packing the bar: (12,6), (6,6), (6,6), (6,6), (12,0), (12,0)
        gaps = 12 + 6 + 6 + 6 + 6 + 6 + 12 + 12
        assert sum(b.winfo_reqwidth() for b in buttons) + gaps <= 1080 - 48
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


# --- compact cover tiles -----------------------------------------------------


def _fully_visible_rows(grid: SaveTileGrid) -> int:
    """Number of tile rows rendered completely inside the viewport."""
    viewport = grid._viewport_height()
    rows: dict = {}
    for (_x1, y1, _x2, y2) in grid._boxes:
        key = round(y1)
        rows.setdefault(key, True)
        if y2 > viewport:
            rows[key] = False
    return sum(1 for ok in rows.values() if ok)


def test_grid_uses_compact_tile_metrics():
    assert SaveTileGrid.TILE_W == ui_theme.TILE_WIDTH
    assert SaveTileGrid.TILE_H == ui_theme.TILE_HEIGHT
    assert SaveTileGrid.GAP == ui_theme.TILE_GAP
    assert SaveTileGrid.TILE_W < 246 and SaveTileGrid.TILE_H < 246


def _many_saves_volume(tmp_path, psp_sfo_bytes, count: int = 16) -> Path:
    return _psp_volume(tmp_path, psp_sfo_bytes, [f"ULJM{i:05d}" for i in range(count)])


def _app_with_volume(tk_root, tmp_path, psp_sfo_bytes, count=16):
    vol = _many_saves_volume(tmp_path, psp_sfo_bytes, count)
    state = AppState(
        provider=FakeVolumeProvider([VolumeInfo(name="PSP", mount_point=vol)]),
        library_root=tmp_path / "lib",
    )
    state.refresh_volumes()
    app = build_app(state=state, root=tk_root)
    state.select_mount(vol)
    app.refresh_saves_ui()
    return app


def test_default_window_shows_four_columns_and_three_rows(tk_root, tmp_path, psp_sfo_bytes):
    app = _app_with_volume(tk_root, tmp_path, psp_sfo_bytes)
    try:
        _map_root(tk_root)
        tk_root.geometry("1180x740")
        tk_root.update()
        tk_root.update()
        grid = app.save_list
        assert grid.size() == 16
        assert grid.columns() == 4
        rows = _fully_visible_rows(grid)
        assert rows >= 3
        assert grid.columns() * rows >= 12
    finally:
        _dispose(app, tk_root)
        _unmap_root(tk_root)


def test_min_window_keeps_at_least_three_columns(tk_root, tmp_path, psp_sfo_bytes):
    app = _app_with_volume(tk_root, tmp_path, psp_sfo_bytes)
    try:
        _map_root(tk_root)
        tk_root.geometry("1080x680")
        tk_root.update()
        tk_root.update()
        assert app.save_list.columns() >= 3
    finally:
        _dispose(app, tk_root)
        _unmap_root(tk_root)


def test_cover_tile_draws_image_and_fallback_draws_monogram(tk_root, tmp_path):
    from PIL import Image

    cover_path = tmp_path / "cover.png"
    Image.new("RGBA", (80, 80), (10, 120, 255, 255)).save(cover_path)

    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="With Cover",
        path="/tmp/vol/PSP/SAVEDATA/ULJM00001",
        title_id="ULJM00001",
    )
    covered = tile_face(entry, "new")
    covered["cover"] = cover_path
    fallback = tile_face(entry, "new")  # no "cover" key -> pastel + monogram

    grid = _grid(tk_root, faces=[covered, fallback])
    try:
        grid.update_idletasks()
        assert grid.find_withtag("tile-cover")
        assert grid.find_withtag("tile-mono")  # the fallback tile still shows a letter
    finally:
        grid.destroy()


def test_cover_cache_is_negative_and_bounded(tk_root, tmp_path):
    grid = _grid(tk_root, faces=[])
    try:
        corrupt = tmp_path / "bad.png"
        corrupt.write_bytes(b"not an image")
        assert grid._cover_photo(corrupt, 40, 40) is None
        # A decode failure is cached as a negative entry instead of re-decoding.
        assert list(grid._cover_cache.values()) == [None]
        assert len(grid._cover_cache) == 1

        grid.COVER_CACHE_MAX = 3
        for i in range(5):
            path = tmp_path / f"c{i}.png"
            path.write_bytes(b"garbage")
            grid._cover_photo(path, 10, 10)
        assert len(grid._cover_cache) <= 3
    finally:
        grid.destroy()


def test_resolve_cover_priority_flows_into_faces(tk_root, tmp_path, psp_sfo_bytes):
    from PIL import Image

    vol = _psp_volume(tmp_path, psp_sfo_bytes, ["ULJM05800"])
    lib = tmp_path / "lib"
    state = AppState(
        provider=FakeVolumeProvider([VolumeInfo(name="PSP", mount_point=vol)]),
        library_root=lib,
    )
    state.refresh_volumes()
    state.select_mount(vol)
    # Drop a user cover for the scanned save.
    cover_dir = lib / "covers" / "psp"
    cover_dir.mkdir(parents=True)
    Image.new("RGB", (32, 32), (0, 200, 0)).save(cover_dir / "ULJM05800.png")

    app = build_app(state=state, root=tk_root)
    try:
        app.refresh_saves_ui()
        assert app.save_list._tiles[0].get("cover") == cover_dir / "ULJM05800.png"
    finally:
        _dispose(app, tk_root)

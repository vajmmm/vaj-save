"""Behavioral tests for the quiet single-column save list and inspector UI.

The pure row-data helper lives in ``tests/test_ui_theme.py``; here we exercise
the list selection semantics (click / ctrl-click / shift-click / arrows /
Ctrl-A / double click), the row rendering, and the wiring into ``VajSaveApp``
(single-column list + right-hand inspector).
"""

from __future__ import annotations

import inspect
import re
import time
from pathlib import Path

import pytest

from vajsave import app_ui
from vajsave import ui_theme
from vajsave.app_ui import CanvasButton, build_app
from vajsave.app_state import AppState
from vajsave.models import SaveEntry, VolumeInfo
from vajsave.ui_theme import SWITCH
from vajsave.volume import FakeVolumeProvider

PROJECT_ROOT = Path(inspect.getsourcefile(app_ui)).resolve().parents[2]

FORBIDDEN_LABELS = (
    "备份所选",
    "备份当前列表",
    "收藏",
    "只看收藏",
    "在访达中显示",
    "在文件管理器中显示",
)

_CLOCK_RE = re.compile(r"^\d{1,2}:\d{2}$")


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


def _descendants(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _descendants(child)


def _all_widget_texts(widget):
    texts = []
    for w in _descendants(widget):
        if isinstance(w, CanvasButton):
            texts.append(w._text)
            continue
        try:
            texts.append(str(w.cget("text")))
        except Exception:
            continue
    return texts


def _rows(n: int = 5):
    rows = []
    for i in range(n):
        entry = SaveEntry(
            platform="psp",
            source_id="psp",
            display_name=f"Game {i}",
            path=f"/tmp/vol/PSP/SAVEDATA/ULJM{i:05d}",
            title_id=f"ULJM{i:05d}",
        )
        status = "new" if i % 2 == 0 else "changed"
        rows.append(ui_theme.save_row(entry, status))
    return rows


def _grid(root, rows=None, on_select=None, on_activate=None):
    grid = app_ui.SaveList(root, on_select=on_select, on_activate=on_activate, width=520, height=400)
    grid.pack()
    root.update_idletasks()
    grid.set_rows(rows if rows is not None else _rows())
    root.update_idletasks()
    return grid


# --- list model -------------------------------------------------------------


def test_list_is_single_column(tk_root):
    grid = _grid(tk_root)
    try:
        assert grid.size() == 5
        assert grid.curselection() == ()
        bounds = {(round(x1), round(x2)) for x1, _y1, x2, _y2 in grid._boxes}
        assert len(bounds) == 1, "every row must share the same horizontal span"
    finally:
        grid.destroy()


def test_widgets_live_in_ui_widgets_module():
    from vajsave import ui_widgets

    # The standalone widgets moved next to their tokens; ``app_ui`` re-exports
    # them so existing imports keep working.
    assert app_ui.CanvasButton is ui_widgets.CanvasButton
    assert app_ui.SaveList is ui_widgets.SaveList
    assert app_ui.ui_font is ui_widgets.ui_font
    assert app_ui._truncate_ui_text is ui_widgets._truncate_ui_text
    # The app shell itself stays in ``app_ui``.
    assert not hasattr(ui_widgets, "VajSaveApp")


def test_no_tile_pill_star_bar_or_monogram_items(tk_root):
    grid = _grid(tk_root)
    try:
        for tag in ("tile", "tile-pill", "tile-star", "tile-bar", "tile-mono", "tile-ring"):
            assert grid.find_withtag(tag) == (), tag
        for tag in ("row-pill", "row-ring", "row-star", "row-bar", "row-mono"):
            assert grid.find_withtag(tag) == (), tag
        assert grid.find_withtag("row-title") != ()
    finally:
        grid.destroy()


def test_status_is_rendered_as_text_not_a_pill(tk_root):
    grid = _grid(tk_root, rows=_rows(2))
    try:
        statuses = grid.find_withtag("row-status")
        assert statuses
        labels = {grid.itemcget(item, "text") for item in statuses}
        assert "新" in labels and "有变化" in labels
        # A pill would be a filled polygon; verify there are no filled status shapes.
        assert grid.find_withtag("row-pill") == ()
    finally:
        grid.destroy()


def test_row_edge_is_neutral_and_three_px_wide(tk_root):
    grid = _grid(tk_root, rows=_rows(2))
    try:
        edges = grid.find_withtag("row-edge")
        assert edges
        for item in edges:
            x1, _y1, x2, _y2 = grid.coords(item)
            assert round(x2 - x1) == ui_theme.ROW_PIP_WIDTH == 3
            assert grid.itemcget(item, "fill") == SWITCH["line_soft"]
    finally:
        grid.destroy()


def test_placeholder_square_and_cover_image(tk_root, tmp_path):
    from PIL import Image

    cover_path = tmp_path / "cover.png"
    Image.new("RGBA", (80, 80), (10, 120, 255, 255)).save(cover_path)

    covered = ui_theme.save_row(
        SaveEntry(platform="psp", source_id="psp", display_name="With Cover", path="/tmp/a"),
        "new",
    )
    covered["cover"] = cover_path
    fallback = ui_theme.save_row(
        SaveEntry(platform="psp", source_id="psp", display_name="No Cover", path="/tmp/b"),
        "new",
    )

    grid = _grid(tk_root, rows=[covered, fallback])
    try:
        grid.update_idletasks()
        assert grid.find_withtag("row-cover")
        placeholders = grid.find_withtag("row-placeholder")
        assert placeholders
        # The empty cover slot is a plain light-grey square, never a monogram.
        assert grid.itemcget(placeholders[0], "fill") == SWITCH["surface_alt"]
        assert grid.find_withtag("row-mono") == ()
    finally:
        grid.destroy()


def test_selected_row_does_not_grow_and_has_no_ring(tk_root):
    grid = _grid(tk_root)
    try:
        before = grid.coords(grid.find_withtag("edge0")[0])
        grid.select_index(0)
        after = grid.coords(grid.find_withtag("edge0")[0])
        assert before == after
        assert grid.find_withtag("row-ring") == ()
    finally:
        grid.destroy()


# --- selection semantics ----------------------------------------------------


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
        grid.select_index(0, extend=True)
        assert grid.curselection() == (0, 1)
    finally:
        grid.destroy()


def test_arrows_move_selection_by_one_row(tk_root):
    grid = _grid(tk_root)
    try:
        grid.select_index(0)
        grid.move_active(1)
        assert grid.curselection() == (1,)
        grid.move_active(1)
        assert grid.curselection() == (2,)
        grid.move_active(-1)
        assert grid.curselection() == (1,)
        # Clamp at both edges instead of wrapping.
        grid.select_index(0)
        grid.move_active(-1)
        assert grid.curselection() == (0,)
        grid.select_index(grid.size() - 1)
        grid.move_active(1)
        assert grid.curselection() == (grid.size() - 1,)
    finally:
        grid.destroy()


def test_arrow_without_selection_picks_first(tk_root):
    grid = _grid(tk_root, rows=_rows(3))
    try:
        grid.move_active(1)
        assert grid.curselection() == (0,)
    finally:
        grid.destroy()


def test_ctrl_a_selects_all(tk_root):
    grid = _grid(tk_root, rows=_rows(3))
    try:
        assert grid.bind("<Control-a>")
        assert grid.bind("<Command-a>")
        grid.select_all()
        assert grid.curselection() == (0, 1, 2)
    finally:
        grid.destroy()


def test_ctrl_a_event_selects_all(tk_root):
    grid = _grid(tk_root, rows=_rows(3))
    try:
        _map_root(tk_root)
        grid.focus_set()
        tk_root.update()
        import sys

        sequence = "<Command-a>" if sys.platform == "darwin" else "<Control-a>"
        grid.event_generate(sequence)
        tk_root.update()
        assert grid.curselection() == (0, 1, 2)
        _unmap_root(tk_root)
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


def test_empty_list_renders_hint_without_error(tk_root):
    grid = _grid(tk_root, rows=[])
    try:
        assert grid.size() == 0
        assert grid.curselection() == ()
        grid.set_hover(3)
        assert grid.hover_index is None
        grid.select_index(0)
        assert grid.curselection() == ()
    finally:
        grid.destroy()


def test_hover_uses_hover_token(tk_root):
    grid = _grid(tk_root)
    try:
        _map_root(tk_root)
        cx, cy = grid._boxes[0][0] + 5, int((grid._boxes[0][1] + grid._boxes[0][3]) / 2)
        grid.event_generate("<Motion>", x=cx, y=cy)
        assert grid.hover_index == 0
        fills = [grid.itemcget(i, "fill") for i in grid.find_all()]
        assert SWITCH["hover"] in fills
        grid.event_generate("<Leave>")
        fills = [grid.itemcget(i, "fill") for i in grid.find_all()]
        assert SWITCH["hover"] not in fills
        _unmap_root(tk_root)
    finally:
        grid.destroy()


def test_two_hundred_row_layout_stays_bounded(tk_root):
    grid = _grid(tk_root, rows=_rows(200))
    try:
        assert len(grid._boxes) == 200
        start = time.perf_counter()
        grid._layout()
        assert (time.perf_counter() - start) * 1000 < 1000.0
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


def _app_with_volume(tk_root, tmp_path, psp_sfo_bytes, title_ids=("ULJM05800", "ULJM05801")):
    vol = _psp_volume(tmp_path, psp_sfo_bytes, list(title_ids))
    state = AppState(
        provider=FakeVolumeProvider([VolumeInfo(name="PSP", mount_point=vol)]),
        library_root=tmp_path / "lib",
    )
    state.refresh_volumes()
    app = build_app(state=state, root=tk_root)
    state.select_mount(vol)
    app.refresh_saves_ui()
    return app, state


def test_tile_grid_widget_is_gone():
    assert not hasattr(app_ui, "SaveTileGrid")
    assert hasattr(app_ui, "SaveList")


def test_enrichment_entrypoints_share_one_worker(tk_root, tmp_path):
    from vajsave.identity import GameIdentity, resolved

    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        entry = SaveEntry(
            platform="gba", source_id="gba", display_name="Apotris", path="/tmp/Apotris.sav"
        )
        identity = GameIdentity(identity_key="gba:sha1:aa", platform="gba", title="Apotris", rom_sha1="aa")
        result = resolved(identity)

        # Both the inspector and the eager list must funnel through the one
        # shared worker instead of each defining its own task closure.
        calls = []
        app._submit_enrichment = lambda save, res, callback: calls.append((save.path, res))
        app._request_enrichment(entry, result)
        app._submit_row_enrichment(entry, result, app._list_generation)
        assert calls == [(entry.path, result), (entry.path, result)]
    finally:
        _dispose(app, tk_root)


def test_app_list_matches_visible_saves_and_wires_selection(tk_root, tmp_path, psp_sfo_bytes):
    import tkinter as tk

    app, state = _app_with_volume(tk_root, tmp_path, psp_sfo_bytes, ["ULJM05800", "ULJM05801", "ULJM05802"])
    try:
        assert isinstance(app.save_list, app_ui.SaveList)
        assert app.save_list.size() == len(state.visible_saves()) == 3
        assert len(app._saves_index) == 3

        # Device + versions lists stay native Listboxes.
        assert isinstance(app.vol_list, tk.Listbox)
        assert isinstance(app.version_list, tk.Listbox)

        app.save_list.select_index(1)
        assert app._selected_save is app._saves_index[1]
        assert app.detail_vars["path"].get() == app._saves_index[1].path
    finally:
        _dispose(app, tk_root)


def test_app_uses_switch_basic_white_surfaces(tk_root, tmp_path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        assert app.root.cget("bg") == SWITCH["window"]
        assert app.detail_panel.cget("bg") == SWITCH["border_soft"]
        assert app.save_list.cget("bg") == SWITCH["window"]
    finally:
        _dispose(app, tk_root)


def test_only_accent_fill_is_the_backup_button(tk_root, tmp_path):
    import tkinter as tk

    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        accents = [b._text for b in app._action_buttons if b.accent]
        assert accents == ["备份存档"]
        for widget in _descendants(app.root):
            if isinstance(widget, tk.Listbox):
                assert widget.cget("selectbackground") != SWITCH["accent"]
            try:
                background = str(widget.cget("bg"))
                widget_width = int(widget.cget("width"))
            except Exception:
                continue
            if background != SWITCH["accent"]:
                continue
            # The only saturated accent surface at this level is the primary action.
            assert widget_width <= 3, type(widget)
    finally:
        _dispose(app, tk_root)


def test_right_panel_has_only_three_small_action_buttons(tk_root, tmp_path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        texts = [b._text for b in app._action_buttons]
        assert texts == ["备份存档", "恢复", "导出 ZIP"]
        assert [b.accent for b in app._action_buttons] == [True, False, False]
        assert app._action_buttons[0].grid_info().get("sticky") == "ew"
        for button in app._action_buttons[1:]:
            assert button.winfo_reqwidth() < 200
    finally:
        _dispose(app, tk_root)


def test_detail_is_a_definition_list_and_versions_take_remaining_height(tk_root, tmp_path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        for key in ("platform", "identity", "status", "source_mtime", "last_backup", "path"):
            assert key in app.detail_vars
        info = app.versions_frame.grid_info()
        assert set(info.get("sticky") or "") == set("nsew")
        assert int(app.versions_frame.grid_rowconfigure(1)["weight"]) == 1
        # The old full-width path strip is gone.
        assert not hasattr(app, "path_entry_var")
    finally:
        _dispose(app, tk_root)


def test_topbar_has_no_round_badge_or_clock(tk_root, tmp_path):
    import tkinter as tk

    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        assert not hasattr(app, "clock_var")
        assert not hasattr(app, "_tick_clock")
        for widget in _descendants(app.topbar):
            if isinstance(widget, tk.Canvas):
                items = widget.find_all()
                kinds = {widget.type(item) for item in items}
                assert "oval" not in kinds, "round badge must be gone"
            try:
                text = str(widget.cget("text"))
            except Exception:
                continue
            assert not _CLOCK_RE.match(text.strip()), text
    finally:
        _dispose(app, tk_root)


def test_subtitle_fully_visible_at_default_and_min_window(tk_root, tmp_path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        _map_root(tk_root)
        for geometry in ("1320x780", "1280x760"):
            tk_root.geometry(geometry)
            tk_root.update()
            tk_root.update()
            label = app.subtitle_label
            assert label.winfo_width() >= label.winfo_reqwidth()
            assert label.winfo_width() > 1
        _unmap_root(tk_root)
    finally:
        _dispose(app, tk_root)


def test_forbidden_labels_absent_from_ui_and_source(tk_root, tmp_path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        texts = _all_widget_texts(app.root)
        joined = " ".join(texts)
        for label in FORBIDDEN_LABELS:
            assert label not in joined, label
    finally:
        _dispose(app, tk_root)

    source = Path(inspect.getsourcefile(app_ui)).read_text(encoding="utf-8")
    for label in FORBIDDEN_LABELS:
        assert label not in source, label


def test_backup_uses_only_current_multi_selection(tk_root, tmp_path, psp_sfo_bytes):
    app, state = _app_with_volume(tk_root, tmp_path, psp_sfo_bytes)
    try:
        captured = []
        state.import_selected_saves = lambda entries: captured.append(list(entries)) or []
        app.save_list.selection_set(0, 1)
        app.on_backup_clicked()
        assert len(captured) == 1
        assert len(captured[0]) == 2
    finally:
        _dispose(app, tk_root)


def test_backup_without_selection_warns_and_imports_nothing(tk_root, tmp_path, psp_sfo_bytes):
    app, state = _app_with_volume(tk_root, tmp_path, psp_sfo_bytes)
    try:
        captured = []
        state.import_selected_saves = lambda entries: captured.append(list(entries)) or []
        app.save_list.selection_clear()
        app.on_backup_clicked()
        assert captured == []
        assert "先" in app.warning_label_var.get()
    finally:
        _dispose(app, tk_root)


def test_double_click_backs_up_clicked_save(tk_root, tmp_path, psp_sfo_bytes):
    app, state = _app_with_volume(tk_root, tmp_path, psp_sfo_bytes, ["ULJM05800"])
    try:
        assert app.save_list.size() == 1
        app.save_list.activate_index(0)
        assert state.last_backup is not None
        assert state.last_backup.snapshot is not None
    finally:
        _dispose(app, tk_root)


def test_bottom_bar_fits_at_min_width(tk_root, tmp_path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        tk_root.update_idletasks()
        buttons = getattr(app, "_bottom_buttons", None)
        assert buttons
        gaps = 12 + 6 + 6 + 6 + 6 + 6 + 12 + 12
        assert sum(b.winfo_reqwidth() for b in buttons) + gaps <= 1080 - 48
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


def test_updated_only_filter_label_default_and_status(tk_root, tmp_path):
    """The filter reads "仅显示有更新", is off by default, and mirrors state in status."""
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        assert app._hide_unchanged_button._text == "仅显示有更新"
        assert state.hide_unchanged is False
        assert app._hide_unchanged_button.selected is False

        app._hide_unchanged_button.invoke()
        assert state.hide_unchanged is True
        assert app._hide_unchanged_button.selected is True
        assert app.status_label_var.get() == "仅显示有更新"

        app._hide_unchanged_button.invoke()
        assert state.hide_unchanged is False
        assert app._hide_unchanged_button.selected is False
        assert app.status_label_var.get() == "显示全部存档"
    finally:
        _dispose(app, tk_root)


def test_refresh_platform_ui_reuses_rows(tk_root, tmp_path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        app.refresh_platform_ui()
        first = {key: row["row"].winfo_id() for key, row in app._platform_rows.items()}
        app.refresh_platform_ui()
        second = {key: row["row"].winfo_id() for key, row in app._platform_rows.items()}
        assert first == second
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

        button.set_text("恢复")
        assert button._text == "恢复"

        button.set_selected(True)
        assert button.selected is True
        assert button.state == "selected"

        accent = CanvasButton(tk_root, text="备份", variant="accent")
        assert accent.accent is True
        assert accent.state == "accent"
        accent.destroy()
    finally:
        button.destroy()


def test_canvas_button_heights_and_measured_width(tk_root):
    bottom = CanvasButton(tk_root, text="刷新", height=CanvasButton.BOTTOM_HEIGHT)
    action = CanvasButton(tk_root, text="备份", height=CanvasButton.ACTION_HEIGHT)
    wide = CanvasButton(tk_root, text="导出 ZIP", height=CanvasButton.ACTION_HEIGHT)
    try:
        assert bottom.winfo_reqheight() == CanvasButton.BOTTOM_HEIGHT
        assert action.winfo_reqheight() == CanvasButton.ACTION_HEIGHT
        assert wide.winfo_reqwidth() > action.winfo_reqwidth()
    finally:
        bottom.destroy()
        action.destroy()
        wide.destroy()


# --- docs / preview consistency ---------------------------------------------


def test_design_and_agents_match_quiet_list_language():
    design = (PROJECT_ROOT / "DESIGN.md").read_text(encoding="utf-8")
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "瓦片" not in design
    assert "药丸" not in design
    assert "status pill" not in design.lower()
    assert "单列" in design
    assert "单列" in agents or "列表" in agents


def test_ui_preview_has_no_tile_vocabulary():
    preview = (PROJECT_ROOT / "scripts" / "ui_preview.py").read_text(encoding="utf-8")
    assert "TILE_" not in preview
    assert "tile_face" not in preview
    assert "grid_columns" not in preview

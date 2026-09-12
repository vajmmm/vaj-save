"""The settings window reads and writes the per-library keep_last setting.

``保留版本数`` (``keep_last``) lives in the library's ``settings.json``; ``0``
means unlimited. The dialog must round-trip a valid value and must never leave
the file corrupt when the entered value is rejected.
"""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path

import pytest

from vajsave import app_ui
from vajsave.app_ui import CanvasButton, build_app
from vajsave.app_state import AppState
from vajsave.library import load_keep_last, settings_path
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


def _app(tk_root, tmp_path):
    lib = tmp_path / "lib"
    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    app = build_app(state=state, root=tk_root)
    return app, state


def _open_settings(app, tk_root):
    before = {c for c in tk_root.winfo_children() if isinstance(c, tk.Toplevel)}
    app.on_settings_clicked()
    dialogs = [
        c
        for c in tk_root.winfo_children()
        if isinstance(c, tk.Toplevel) and c not in before
    ]
    assert dialogs, "settings dialog did not open"
    return dialogs[-1]


def _descendants(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _descendants(child)


def _find_button(widget, label: str) -> CanvasButton:
    for w in _descendants(widget):
        if isinstance(w, CanvasButton) and w._text == label:
            return w
    raise AssertionError(f"button {label!r} not found")


def test_settings_dialog_reads_current_keep_last(tk_root, tmp_path):
    app, state = _app(tk_root, tmp_path)
    try:
        state.library_root.mkdir(parents=True, exist_ok=True)
        settings_path(state.library_root).write_text(
            json.dumps({"keep_last": 4}), encoding="utf-8"
        )
        dialog = _open_settings(app, tk_root)
        assert dialog.keep_last_var.get() == "4"
    finally:
        _dispose(app, tk_root)


def test_settings_save_persists_valid_keep_last(tk_root, tmp_path):
    app, state = _app(tk_root, tmp_path)
    try:
        dialog = _open_settings(app, tk_root)
        dialog.keep_last_var.set("3")
        _find_button(dialog, "保存").invoke()
        assert load_keep_last(state.library_root) == 3
    finally:
        _dispose(app, tk_root)


def test_settings_save_zero_means_unlimited(tk_root, tmp_path):
    app, state = _app(tk_root, tmp_path)
    try:
        dialog = _open_settings(app, tk_root)
        dialog.keep_last_var.set("0")
        _find_button(dialog, "保存").invoke()
        assert load_keep_last(state.library_root) == 0
    finally:
        _dispose(app, tk_root)


def test_settings_invalid_keep_last_preserves_file_and_stays_open(tk_root, tmp_path):
    app, state = _app(tk_root, tmp_path)
    try:
        state.library_root.mkdir(parents=True, exist_ok=True)
        settings_path(state.library_root).write_text(
            json.dumps({"keep_last": 5, "note": "keep-me"}), encoding="utf-8"
        )
        dialog = _open_settings(app, tk_root)
        dialog.keep_last_var.set("abc")
        _find_button(dialog, "保存").invoke()

        data = json.loads(settings_path(state.library_root).read_text(encoding="utf-8"))
        assert data["keep_last"] == 5
        assert data["note"] == "keep-me"
        assert load_keep_last(state.library_root) == 5
        # The dialog stays open so the user can correct the value.
        assert dialog in tk_root.winfo_children()
    finally:
        _dispose(app, tk_root)


def test_settings_negative_keep_last_is_rejected(tk_root, tmp_path):
    app, state = _app(tk_root, tmp_path)
    try:
        dialog = _open_settings(app, tk_root)
        dialog.keep_last_var.set("-2")
        _find_button(dialog, "保存").invoke()
        # Nothing was written, so the load still falls back to the default.
        from vajsave.library import DEFAULT_KEEP_LAST

        assert load_keep_last(state.library_root) == DEFAULT_KEEP_LAST
    finally:
        _dispose(app, tk_root)

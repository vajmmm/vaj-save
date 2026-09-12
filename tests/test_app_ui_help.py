"""The 帮助 button opens a short guide that states the copy direction.

The guide must make it explicit that the app backs saves up to the computer and
does not write to the handheld console, replacing the old placeholder status
("帮助中心暂未配置").
"""

from __future__ import annotations

import tkinter as tk

import pytest

from vajsave import app_ui
from vajsave.app_ui import build_app
from vajsave.app_state import AppState
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
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    return build_app(state=state, root=tk_root)


def _descendants(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _descendants(child)


def _collect_text(widget) -> str:
    parts = []
    for w in _descendants(widget):
        try:
            text = w.cget("text")
        except Exception:
            continue
        if text:
            parts.append(str(text))
    return "\n".join(parts)


def _open_help(app, tk_root):
    before = {c for c in tk_root.winfo_children() if isinstance(c, tk.Toplevel)}
    app.help_button.invoke()
    dialogs = [
        c
        for c in tk_root.winfo_children()
        if isinstance(c, tk.Toplevel) and c not in before
    ]
    assert dialogs, "help window did not open"
    return dialogs[-1]


def test_help_button_opens_short_guide(tk_root, tmp_path):
    app = _app(tk_root, tmp_path)
    try:
        dialog = _open_help(app, tk_root)
        text = _collect_text(dialog)
        assert "不会写入掌机" in text
        # Old placeholder must be gone from both the dialog and the status line.
        assert "帮助中心暂未配置" not in text
        assert "帮助中心暂未配置" not in app.status_label_var.get()
    finally:
        _dispose(app, tk_root)


def test_help_guide_mentions_backup_and_restore_to_folder(tk_root, tmp_path):
    app = _app(tk_root, tmp_path)
    try:
        dialog = _open_help(app, tk_root)
        text = _collect_text(dialog)
        assert "备份" in text
        assert "文件夹" in text
    finally:
        _dispose(app, tk_root)


def test_help_guide_mentions_ftp_pull_and_presets(tk_root, tmp_path):
    app = _app(tk_root, tmp_path)
    try:
        dialog = _open_help(app, tk_root)
        text = _collect_text(dialog)
        assert "FTP" in text
        # The default preset and its fallback are both named.
        assert "Checkpoint" in text
        assert "ftpd" in text
    finally:
        _dispose(app, tk_root)


def test_help_guide_points_to_the_llm_cover_settings(tk_root, tmp_path):
    app = _app(tk_root, tmp_path)
    try:
        dialog = _open_help(app, tk_root)
        text = _collect_text(dialog)
        # The guide points at the dedicated dialog instead of the old inline
        # fields, which no longer live in the main settings window.
        assert "LLM 封面消歧" in text
        assert "设置" in text
    finally:
        _dispose(app, tk_root)

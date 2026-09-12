"""The 恢复 action copies a version into a chosen folder after confirming.

Restoring must never silently copy: a cancelled confirmation copies nothing, and
the confirmation text has to make clear that the version is copied to a folder
and the handheld console is not written to. ``restore_snapshot`` semantics stay
untouched (the library copy is preserved).
"""

from __future__ import annotations

from datetime import datetime

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


def _app_with_version(tk_root, tmp_path):
    lib = tmp_path / "lib"
    folder = tmp_path / "src" / "psp" / "ULJM05800"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "save.bin").write_bytes(b"payload")
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="PSP Game",
        path=str(folder),
        title_id="ULJM05800",
    )
    backup_save(entry, lib, when=datetime(2024, 1, 1, 10, 0, 0))
    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    app = build_app(state=state, root=tk_root)
    snapshot = load_catalog(lib).games[game_key(entry)].versions[0]
    app._selected_snapshot = snapshot
    return app, state, snapshot


def test_cancelled_restore_confirmation_copies_nothing(tk_root, tmp_path, monkeypatch):
    app, state, _snapshot = _app_with_version(tk_root, tmp_path)
    try:
        called = []
        monkeypatch.setattr(app_ui.messagebox, "askyesno", lambda *a, **k: False)
        monkeypatch.setattr(
            app_ui.filedialog,
            "askdirectory",
            lambda *a, **k: called.append(True) or str(tmp_path / "dst"),
        )
        app.on_restore_clicked()
        assert called == [], "cancelled confirmation must not open the folder dialog"
        assert not (tmp_path / "dst").exists()
    finally:
        _dispose(app, tk_root)


def test_restore_confirmation_mentions_folder_and_no_handheld_write(
    tk_root, tmp_path, monkeypatch
):
    app, state, _snapshot = _app_with_version(tk_root, tmp_path)
    try:
        captured = {}

        def fake_askyesno(title, message, **kwargs):
            captured["title"] = title
            captured["message"] = message
            return False

        monkeypatch.setattr(app_ui.messagebox, "askyesno", fake_askyesno)
        app.on_restore_clicked()

        assert "文件夹" in captured["message"]
        assert "不会写入掌机" in captured["message"]
    finally:
        _dispose(app, tk_root)


def test_confirmed_restore_copies_snapshot_to_folder(tk_root, tmp_path, monkeypatch):
    app, state, snapshot = _app_with_version(tk_root, tmp_path)
    try:
        dest = tmp_path / "restore-target"
        dest.mkdir()
        monkeypatch.setattr(app_ui.messagebox, "askyesno", lambda *a, **k: True)
        monkeypatch.setattr(app_ui.filedialog, "askdirectory", lambda *a, **k: str(dest))

        app.on_restore_clicked()

        copied = list(dest.rglob("save.bin"))
        assert copied, "restore should copy the snapshot payload into the folder"
        # The library copy is untouched: restore never prunes existing versions.
        assert snapshot.absolute_path(state.library_root).exists()
    finally:
        _dispose(app, tk_root)


def test_restore_without_selected_version_warns(tk_root, tmp_path, monkeypatch):
    app, state, _snapshot = _app_with_version(tk_root, tmp_path)
    try:
        app._selected_snapshot = None
        called = []
        monkeypatch.setattr(
            app_ui.messagebox, "askyesno", lambda *a, **k: called.append(True) or True
        )
        app.on_restore_clicked()
        assert called == []
        assert "先" in app.warning_label_var.get()
    finally:
        _dispose(app, tk_root)

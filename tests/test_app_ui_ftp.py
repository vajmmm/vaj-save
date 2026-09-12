"""UI wiring tests for pulling saves over FTP from a device preset.

Mirrors the style of ``test_app_ui_help.py``: build a real (withdrawn) Tk app,
invoke the FTP button, and assert what the dialog offers and what a pull does.
The FTP transport itself is a deterministic fake injected into ``AppState``.
"""

from __future__ import annotations

import tkinter as tk

import pytest

from vajsave.app_ui import build_app
from vajsave.app_state import AppState
from vajsave.ftp_fetch import ftp_cache_root
from vajsave.volume import FakeVolumeProvider

from conftest import checkpoint_ftp_tree, fake_client_factory


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


def _app(tk_root, tmp_path, factory):
    state = AppState(
        provider=FakeVolumeProvider([]),
        library_root=tmp_path / "lib",
        ftp_client_factory=factory,
    )
    return build_app(state=state, root=tk_root)


def _open_ftp(app, tk_root):
    before = {c for c in tk_root.winfo_children() if isinstance(c, tk.Toplevel)}
    app.ftp_button.invoke()
    dialogs = [
        c
        for c in tk_root.winfo_children()
        if isinstance(c, tk.Toplevel) and c not in before
    ]
    assert dialogs, "FTP window did not open"
    return dialogs[-1]


def test_ftp_button_opens_dialog_with_both_presets(tk_root, tmp_path):
    app = _app(tk_root, tmp_path, fake_client_factory(checkpoint_ftp_tree()))
    try:
        dialog = _open_ftp(app, tk_root)
        text = _collect_text(dialog)
        # The guide copy names both presets...
        assert "Checkpoint" in text
        assert "ftpd" in text
        # ...and the preset buttons offer exactly those keys, defaulting to Checkpoint.
        buttons = dialog.ftp_preset_buttons
        assert set(buttons) == {"checkpoint", "ftpd"}
        assert buttons["checkpoint"]._text == "Checkpoint"
        assert buttons["ftpd"]._text == "ftpd"
        assert buttons["checkpoint"].selected is True
        assert dialog.ftp_preset_var.get() == "checkpoint"
    finally:
        _dispose(app, tk_root)


def test_ftp_pull_from_dialog_scans_cache_as_device(tk_root, tmp_path):
    factory = fake_client_factory(checkpoint_ftp_tree())
    app = _app(tk_root, tmp_path, factory)
    try:
        dialog = _open_ftp(app, tk_root)
        dialog.ftp_host_var.set("192.168.1.50")
        dialog.ftp_pull()

        expected = ftp_cache_root(tmp_path / "lib") / "checkpoint"
        assert app.state.current_mount == expected
        assert app.state.current_result is not None
        assert app.state.current_result.saves
        assert "FTP" in app.status_label_var.get()
        assert "192.168.1.50" not in app.status_label_var.get()
    finally:
        _dispose(app, tk_root)


def test_dialog_can_switch_to_ftpd_fallback_preset(tk_root, tmp_path):
    factory = fake_client_factory(checkpoint_ftp_tree())
    app = _app(tk_root, tmp_path, factory)
    try:
        dialog = _open_ftp(app, tk_root)
        dialog.ftp_preset_var.set("ftpd")
        dialog.ftp_host_var.set("192.168.1.50")
        dialog.ftp_pull()

        assert app.state.ftp_preset_key == "ftpd"
        assert app.state.current_mount == ftp_cache_root(tmp_path / "lib") / "ftpd"
        assert app.state.current_result is not None
        assert app.state.current_result.saves
    finally:
        _dispose(app, tk_root)


def test_dialog_reports_pull_failure_without_selecting_cache(tk_root, tmp_path):
    factory = fake_client_factory(
        checkpoint_ftp_tree(),
        fail_paths={"/3ds/Checkpoint/saves"},
        error_message="permission denied",
    )
    app = _app(tk_root, tmp_path, factory)
    try:
        dialog = _open_ftp(app, tk_root)
        dialog.ftp_host_var.set("192.168.1.50")
        dialog.ftp_pull()

        # A failed pull must not become the selected "device".
        assert app.state.current_mount != ftp_cache_root(tmp_path / "lib") / "checkpoint"
        assert not (ftp_cache_root(tmp_path / "lib") / "checkpoint").exists()
        assert "permission denied" in app.warning_label_var.get()
    finally:
        _dispose(app, tk_root)

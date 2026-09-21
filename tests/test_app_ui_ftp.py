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
from vajsave.library import load_app_config
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


def test_remember_password_writes_ftp_password_only_when_flag_true(tmp_path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    state.configure_ftp(host="10.0.0.1", password="s3cret", remember_password=True)

    saved = load_app_config()
    assert saved.get("ftp_remember_password") is True
    assert saved.get("ftp_password") == "s3cret"
    assert state.ftp_remember_password is True
    assert state._ftp_password == "s3cret"

    state.configure_ftp(password="s3cret", remember_password=False)
    saved = load_app_config()
    assert saved.get("ftp_remember_password") is False
    assert "ftp_password" not in saved
    assert state.ftp_remember_password is False
    assert state._ftp_password == "s3cret"


def test_configure_ftp_default_keeps_password_in_memory_only(tmp_path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    state.configure_ftp(host="10.0.0.1", user="ftp", password="no-store")

    saved = load_app_config()
    assert "ftp_password" not in saved
    assert saved.get("ftp_remember_password") in (False, None)
    assert state._ftp_password == "no-store"
    assert state.ftp_host == "10.0.0.1"


def test_remembered_password_reloads_on_new_appstate(tmp_path):
    first = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    first.configure_ftp(host="192.168.1.50", password="keep-me", remember_password=True)

    fresh = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    assert fresh.ftp_remember_password is True
    assert fresh._ftp_password == "keep-me"
    assert fresh.current_ftp_preset().password == "keep-me"
    assert fresh.ftp_host == "192.168.1.50"

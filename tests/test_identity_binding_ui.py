"""UI wiring for the GameIdentity binding flow in the right-hand inspector.

The resolver/binding backend is covered by ``tests/test_identity.py``; these
tests exercise how the inspector surfaces it:

* an ``ambiguous`` save lists its candidate ROMs and binds the picked one,
* an ``unresolved`` GBA/NDS save opens a manual "选择 ROM" file dialog,
* a successful binding refreshes the UI and the ``manual`` binding persists.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

import pytest

from vajsave import app_ui
from vajsave.app_ui import build_app
from vajsave.app_state import AppState
from vajsave.identity import (
    BINDINGS_NAME,
    STATUS_AMBIGUOUS,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
    GameIdentity,
    resolved,
    unresolved,
)
from vajsave.models import SaveEntry, VolumeInfo
from vajsave.volume import FakeVolumeProvider


def make_gba_rom(title: str = "APOTRIS", code: str = "APTR", size: int = 0x200) -> bytes:
    data = bytearray(size)
    data[0xA0:0xA0 + 12] = title.ljust(12)[:12].encode("ascii")
    data[0xAC:0xB0] = code.ljust(4)[:4].encode("ascii")
    return bytes(data)


@pytest.fixture(scope="module")
def tk_root():
    try:
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


def _gba_volume(tmp_path: Path, rom_names, save_name: str = "Apotris") -> Path:
    """A GBA card: SAVEGAME/<save>.sav plus optional ROMs under GBA/.

    Each ROM is given distinct content so two same-named ROMs stay two genuine
    identities; identical payloads would (correctly) collapse into one game and
    would no longer be an ambiguous match.
    """
    vol = tmp_path / "SD"
    save_dir = vol / "SAVEGAME"
    save_dir.mkdir(parents=True)
    (save_dir / f"{save_name}.sav").write_bytes(b"save-data")
    rom_dir = vol / "GBA"
    rom_dir.mkdir()
    for index, name in enumerate(rom_names):
        (rom_dir / name).write_bytes(
            make_gba_rom(title=Path(name).stem[:12], code=f"R{index:03d}")
        )
    return vol


def _app(tk_root, tmp_path: Path, vol: Path):
    state = AppState(
        provider=FakeVolumeProvider([VolumeInfo(name="SD", mount_point=vol)]),
        library_root=tmp_path / "lib",
    )
    state.refresh_volumes()
    app = build_app(state=state, root=tk_root)
    state.select_mount(vol)
    app.refresh_saves_ui()
    return app, state


# --- ambiguous: list candidates and bind ------------------------------------


def test_ambiguous_save_lists_candidate_roms(tk_root, tmp_path):
    vol = _gba_volume(tmp_path, ["Apotris.gba", "Apotris (Japan).gba"])
    app, state = _app(tk_root, tmp_path, vol)
    try:
        assert app.save_list.size() == 1
        entry = app._saves_index[0]
        result = state.resolve_save_identity(entry)
        assert result.status == STATUS_AMBIGUOUS
        assert len(result.candidates) == 2

        app.save_list.select_index(0)
        tk_root.update_idletasks()
        assert app.identity_frame.winfo_manager() == "grid"
        assert app.identity_list.size() == 2
        assert app._bind_candidate_button.winfo_manager() == "pack"
        assert app._manual_rom_button.winfo_manager() == ""
        # The first candidate is pre-selected so a single click binds.
        assert app.identity_list.curselection() == (0,)
    finally:
        _dispose(app, tk_root)


def test_binding_selected_candidate_refreshes_and_persists(tk_root, tmp_path):
    vol = _gba_volume(tmp_path, ["Apotris.gba", "Apotris (Japan).gba"])
    app, state = _app(tk_root, tmp_path, vol)
    try:
        app.save_list.select_index(0)
        tk_root.update_idletasks()
        candidates = list(state.resolve_save_identity(app._saves_index[0]).candidates)
        chosen = candidates[app.identity_list.curselection()[0]]

        app._bind_candidate_button.invoke()

        assert (state.library_root / BINDINGS_NAME).is_file()
        result = state.resolve_save_identity(app._saves_index[0])
        assert result.status == STATUS_RESOLVED
        assert result.identity_key == chosen.identity_key
        assert result.identity.source == "manual"
        # 绑定后 UI 刷新：识别状态写回详情，绑定区块隐藏。
        assert app.detail_vars["identity"].get() == "已识别"
        assert app.identity_frame.winfo_manager() == ""
    finally:
        _dispose(app, tk_root)


def test_binding_without_candidate_selection_warns(tk_root, tmp_path):
    vol = _gba_volume(tmp_path, ["Apotris.gba", "Apotris (Japan).gba"])
    app, state = _app(tk_root, tmp_path, vol)
    try:
        app.save_list.select_index(0)
        app.identity_list.selection_clear(0, tk.END)
        app._bind_candidate_button.invoke()
        assert "候选" in app.warning_label_var.get()
        assert not (state.library_root / BINDINGS_NAME).is_file()
    finally:
        _dispose(app, tk_root)


# --- unresolved GBA/NDS: manual ROM dialog ----------------------------------


def test_manual_rom_dialog_filters_follow_canonical_extensions():
    assert app_ui._rom_filetypes("gba")[0] == ("GBA ROM", "*.gba *.agb")
    assert app_ui._rom_filetypes("nds")[0] == ("NDS ROM", "*.nds *.ids")
    assert app_ui._rom_filetypes("unknown") == [("所有文件", "*.*")]


def test_unresolved_gba_offers_manual_rom_dialog(tk_root, tmp_path, monkeypatch):
    vol = _gba_volume(tmp_path, [])
    rom = tmp_path / "picked" / "Apotris.gba"
    rom.parent.mkdir()
    rom.write_bytes(make_gba_rom(title="APOTRIS", code="APTR"))
    app, state = _app(tk_root, tmp_path, vol)
    try:
        entry = app._saves_index[0]
        assert state.resolve_save_identity(entry).status == STATUS_UNRESOLVED

        app.save_list.select_index(0)
        tk_root.update_idletasks()
        assert app.identity_frame.winfo_manager() == "grid"
        assert app._manual_rom_button.winfo_manager() == "pack"
        assert app._bind_candidate_button.winfo_manager() == ""

        dialog_calls = []

        def fake_dialog(**kwargs):
            dialog_calls.append(kwargs)
            return str(rom)

        monkeypatch.setattr(app_ui.filedialog, "askopenfilename", fake_dialog)
        app._manual_rom_button.invoke()

        assert dialog_calls, "手动选择 ROM 必须打开文件对话框"
        patterns = " ".join(pattern for _label, pattern in dialog_calls[0]["filetypes"])
        assert "*.gba" in patterns
        result = state.resolve_save_identity(entry)
        assert result.status == STATUS_RESOLVED
        assert result.identity.source == "manual"
        assert result.identity.rom_sha1
        assert app.identity_frame.winfo_manager() == ""
    finally:
        _dispose(app, tk_root)


def test_manual_rom_dialog_cancel_keeps_save_unresolved(tk_root, tmp_path, monkeypatch):
    vol = _gba_volume(tmp_path, [])
    app, state = _app(tk_root, tmp_path, vol)
    try:
        entry = app._saves_index[0]
        app.save_list.select_index(0)
        monkeypatch.setattr(app_ui.filedialog, "askopenfilename", lambda **kwargs: "")
        app._manual_rom_button.invoke()

        # Cancelling the dialog must not bind anything nor warn.
        assert state.resolve_save_identity(entry).status == STATUS_UNRESOLVED
        assert not (state.library_root / BINDINGS_NAME).is_file()
        assert app.warning_label_var.get() == ""
    finally:
        _dispose(app, tk_root)


def test_manual_rom_wrong_extension_is_rejected(tk_root, tmp_path, monkeypatch):
    vol = _gba_volume(tmp_path, [])
    bad = tmp_path / "Apotris.zip"
    bad.write_bytes(make_gba_rom(title="APOTRIS", code="APTR"))
    app, state = _app(tk_root, tmp_path, vol)
    try:
        entry = app._saves_index[0]
        app.save_list.select_index(0)
        monkeypatch.setattr(app_ui.filedialog, "askopenfilename", lambda **kwargs: str(bad))
        app._manual_rom_button.invoke()

        assert state.resolve_save_identity(entry).status == STATUS_UNRESOLVED
        assert not (state.library_root / BINDINGS_NAME).is_file()
        assert "扩展名" in app.warning_label_var.get()
    finally:
        _dispose(app, tk_root)


def test_manual_rom_unreadable_is_rejected(tk_root, tmp_path, monkeypatch):
    vol = _gba_volume(tmp_path, [])
    missing = tmp_path / "Apotris.gba"  # valid extension, but no such file
    app, state = _app(tk_root, tmp_path, vol)
    try:
        entry = app._saves_index[0]
        app.save_list.select_index(0)
        monkeypatch.setattr(app_ui.filedialog, "askopenfilename", lambda **kwargs: str(missing))
        app._manual_rom_button.invoke()

        assert state.resolve_save_identity(entry).status == STATUS_UNRESOLVED
        assert not (state.library_root / BINDINGS_NAME).is_file()
        assert "绑定失败" in app.warning_label_var.get()
    finally:
        _dispose(app, tk_root)


def test_manual_binding_survives_a_fresh_state(tk_root, tmp_path, monkeypatch):
    vol = _gba_volume(tmp_path, [])
    rom = tmp_path / "Apotris.gba"
    rom.write_bytes(make_gba_rom())
    app, state = _app(tk_root, tmp_path, vol)
    try:
        entry = app._saves_index[0]
        app.save_list.select_index(0)
        monkeypatch.setattr(app_ui.filedialog, "askopenfilename", lambda **kwargs: str(rom))
        app._manual_rom_button.invoke()
        key = state.resolve_save_identity(entry).identity_key
        assert key.startswith("gba:sha1:")
    finally:
        _dispose(app, tk_root)

    # A new state on the same library resolves the save with no ROM directory set.
    fresh = AppState(
        provider=FakeVolumeProvider([]),
        library_root=state.library_root,
    )
    result = fresh.resolve_save_identity(entry)
    assert result.status == STATUS_RESOLVED
    assert result.identity_key == key
    assert result.identity.source == "manual"


# --- section visibility ------------------------------------------------------


def test_non_cartridge_unresolved_hides_binding_section(tk_root, tmp_path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        entry = SaveEntry(
            platform="psp",
            source_id="psp",
            display_name="Monster Hunter",
            path="/tmp/PSP/SAVEDATA/ULJM05800",
            title_id="ULJM05800",
        )
        app._update_identity_section(entry, unresolved(reason="no source"))
        assert app.identity_frame.winfo_manager() == ""
    finally:
        _dispose(app, tk_root)


def test_resolved_and_empty_selection_hide_binding_section(tk_root, tmp_path):
    state = AppState(provider=FakeVolumeProvider([]), library_root=tmp_path / "lib")
    app = build_app(state=state, root=tk_root)
    try:
        entry = SaveEntry(platform="gba", source_id="gba", display_name="Apotris", path="/tmp/Apotris.sav")
        identity = GameIdentity(identity_key="gba:sha1:aa", platform="gba", title="Apotris", rom_sha1="aa")
        app._update_identity_section(entry, resolved(identity))
        assert app.identity_frame.winfo_manager() == ""
        app._update_identity_section(None, None)
        assert app.identity_frame.winfo_manager() == ""
    finally:
        _dispose(app, tk_root)

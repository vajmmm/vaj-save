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


# --- optional LLM cover disambiguation ---------------------------------------


def test_settings_dialog_exposes_llm_cover_controls(tk_root, tmp_path):
    app, state = _app(tk_root, tmp_path)
    try:
        dialog = _open_settings(app, tk_root)
        assert hasattr(dialog, "llm_cover_button")
        assert hasattr(dialog, "llm_api_key_var")
        assert dialog.llm_cover_button.selected is False
        assert dialog.llm_api_key_var.get() == ""
        # The key field is masked so it is not shown in cleartext.
        assert dialog.llm_api_key_entry.cget("show") == "\u2022"
    finally:
        _dispose(app, tk_root)


def test_settings_save_persists_llm_cover_and_key(tk_root, tmp_path):
    from vajsave.library import load_app_config

    app, state = _app(tk_root, tmp_path)
    try:
        dialog = _open_settings(app, tk_root)
        dialog.llm_cover_button.invoke()  # toggle the feature on
        assert dialog.llm_cover_button.selected is True
        dialog.llm_api_key_var.set("sk-ui-secret")
        _find_button(dialog, "保存").invoke()

        config = load_app_config()
        assert config["llm_cover_enabled"] is True
        assert config["llm_api_key"] == "sk-ui-secret"
        assert "sk-ui-secret" not in state.status_text
    finally:
        _dispose(app, tk_root)


def test_settings_reads_current_llm_cover_settings(tk_root, tmp_path):
    from vajsave.library import save_app_config

    app, state = _app(tk_root, tmp_path)
    try:
        save_app_config({"llm_cover_enabled": True, "llm_api_key": "sk-existing"})
        state.llm_cover_enabled = True
        state.llm_api_key = "sk-existing"
        dialog = _open_settings(app, tk_root)
        assert dialog.llm_cover_button.selected is True
        assert dialog.llm_api_key_var.get() == "sk-existing"
    finally:
        _dispose(app, tk_root)


def test_settings_dialog_exposes_llm_base_url_and_model(tk_root, tmp_path):
    from vajsave.artwork.llm_choice import DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL

    app, state = _app(tk_root, tmp_path)
    try:
        dialog = _open_settings(app, tk_root)
        assert hasattr(dialog, "llm_base_url_var")
        assert hasattr(dialog, "llm_model_var")
        # Unset settings show the effective defaults rather than a blank field.
        assert dialog.llm_base_url_var.get() == DEFAULT_LLM_BASE_URL
        assert dialog.llm_model_var.get() == DEFAULT_LLM_MODEL
    finally:
        _dispose(app, tk_root)


def test_settings_save_persists_llm_base_url_and_model(tk_root, tmp_path):
    from vajsave.library import load_app_config

    app, state = _app(tk_root, tmp_path)
    try:
        dialog = _open_settings(app, tk_root)
        dialog.llm_base_url_var.set("https://gateway.example/v1")
        dialog.llm_model_var.set("my-model")
        _find_button(dialog, "保存").invoke()

        config = load_app_config()
        assert config["llm_base_url"] == "https://gateway.example/v1"
        assert config["llm_model"] == "my-model"
    finally:
        _dispose(app, tk_root)


def test_settings_reads_current_llm_base_url_and_model(tk_root, tmp_path):
    from vajsave.library import save_app_config

    app, state = _app(tk_root, tmp_path)
    try:
        save_app_config(
            {
                "llm_cover_enabled": True,
                "llm_api_key": "sk-existing",
                "llm_base_url": "http://localhost:11434/v1",
                "llm_model": "llama3",
            }
        )
        state.llm_cover_enabled = True
        state.llm_api_key = "sk-existing"
        state.llm_base_url = "http://localhost:11434/v1"
        state.llm_model = "llama3"
        dialog = _open_settings(app, tk_root)
        assert dialog.llm_base_url_var.get() == "http://localhost:11434/v1"
        assert dialog.llm_model_var.get() == "llama3"
    finally:
        _dispose(app, tk_root)


def test_settings_dialog_exposes_llm_protocol_selector(tk_root, tmp_path):
    from vajsave.artwork.llm_choice import DEFAULT_LLM_PROTOCOL, LLM_PROTOCOLS

    app, state = _app(tk_root, tmp_path)
    try:
        dialog = _open_settings(app, tk_root)
        assert hasattr(dialog, "llm_protocol_var")
        assert dialog.llm_protocol_var.get() == DEFAULT_LLM_PROTOCOL
        values = list(dialog.llm_protocol_combo.cget("values"))
        assert values == list(LLM_PROTOCOLS)
        assert "gemini" not in [str(v).lower() for v in values]
    finally:
        _dispose(app, tk_root)


def test_settings_save_persists_llm_protocol(tk_root, tmp_path):
    from vajsave.artwork.llm_choice import PROTOCOL_ANTHROPIC
    from vajsave.library import load_app_config

    app, state = _app(tk_root, tmp_path)
    try:
        dialog = _open_settings(app, tk_root)
        dialog.llm_protocol_var.set(PROTOCOL_ANTHROPIC)
        _find_button(dialog, "保存").invoke()
        assert load_app_config()["llm_protocol"] == PROTOCOL_ANTHROPIC
        assert state.llm_protocol == PROTOCOL_ANTHROPIC
    finally:
        _dispose(app, tk_root)


def test_settings_reads_current_llm_protocol(tk_root, tmp_path):
    from vajsave.artwork.llm_choice import PROTOCOL_ANTHROPIC
    from vajsave.library import save_app_config

    app, state = _app(tk_root, tmp_path)
    try:
        save_app_config({"llm_protocol": PROTOCOL_ANTHROPIC})
        state.llm_protocol = PROTOCOL_ANTHROPIC
        dialog = _open_settings(app, tk_root)
        assert dialog.llm_protocol_var.get() == PROTOCOL_ANTHROPIC
    finally:
        _dispose(app, tk_root)


# --- no provider preset: the four explicit fields ----------------------------


def test_settings_dialog_has_no_llm_preset_controls(tk_root, tmp_path):
    app, state = _app(tk_root, tmp_path)
    try:
        dialog = _open_settings(app, tk_root)
        # The preset selector is gone; only protocol/base/key/model remain.
        assert not hasattr(dialog, "llm_preset_var")
        assert not hasattr(dialog, "llm_preset_combo")
        assert not hasattr(dialog, "llm_preset_select")
    finally:
        _dispose(app, tk_root)


def test_settings_save_completes_a_base_url_without_v1(tk_root, tmp_path):
    from vajsave.library import load_app_config

    app, state = _app(tk_root, tmp_path)
    try:
        dialog = _open_settings(app, tk_root)
        dialog.llm_base_url_var.set("https://api.deepseek.com")
        _find_button(dialog, "保存").invoke()
        assert load_app_config()["llm_base_url"] == "https://api.deepseek.com/v1"
        assert state.llm_base_url == "https://api.deepseek.com/v1"
    finally:
        _dispose(app, tk_root)


def test_settings_save_persists_the_new_protocol_value(tk_root, tmp_path):
    from vajsave.artwork.llm_choice import PROTOCOL_ANTHROPIC
    from vajsave.library import load_app_config

    app, state = _app(tk_root, tmp_path)
    try:
        dialog = _open_settings(app, tk_root)
        assert dialog.llm_protocol_var.get() == "openai-completions"
        dialog.llm_protocol_var.set(PROTOCOL_ANTHROPIC)
        dialog.llm_base_url_var.set("https://api.anthropic.com")
        _find_button(dialog, "保存").invoke()
        config = load_app_config()
        assert config["llm_protocol"] == PROTOCOL_ANTHROPIC
        # A bare host still gets its /v1 so the messages path resolves.
        assert config["llm_base_url"] == "https://api.anthropic.com/v1"
    finally:
        _dispose(app, tk_root)

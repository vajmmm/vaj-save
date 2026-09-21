"""Unit tests for the pure helpers in ``vajsave.ui_theme``.

These cover the Switch "Basic White" token set plus the row-data helper the
single-column save list relies on. Everything here is display-free so it runs
anywhere, even without a Tk display.
"""

from __future__ import annotations

import pytest

from vajsave import ui_theme
from vajsave.models import SaveEntry


# --- tokens -----------------------------------------------------------------


def test_switch_tokens_are_basic_white():
    tokens = ui_theme.SWITCH
    assert tokens["bg"] == "#ebebeb"
    assert tokens["surface"] == "#f2f2f2"
    assert tokens["surface_alt"] == "#e7e7e7"
    assert tokens["card"] == "#ffffff"
    assert tokens["text"] == "#2d2d2d"
    assert tokens["accent"] == "#0a84ff"
    # No leftover Console Dark surfaces may remain in the main palette.
    assert {"#1c1c1e", "#2c2c2e", "#3a3a3c"}.isdisjoint(set(tokens.values()))


def test_platform_colors_are_canonical_and_hex():
    for key in ("all", "psp", "vita", "switch", "3ds", "nds", "gb", "gbc", "gba"):
        value = ui_theme.PLATFORM_COLORS[key]
        assert value.startswith("#") and len(value) == 7


def test_switch_functional_tokens_match_spec():
    tokens = ui_theme.SWITCH
    assert tokens["muted"] == "#8b8b8b"
    assert tokens["success"] == "#30d158"
    assert tokens["warning"] == "#ff9f0a"
    assert tokens["danger"] == "#ff453a"
    for key in ("hover", "line_strong", "accent_hover"):
        value = tokens[key]
        assert value.startswith("#") and len(value) == 7


def test_switch_module_level_tokens_exist():
    for name in ("HOVER", "LINE_STRONG", "ACCENT_HOVER"):
        value = getattr(ui_theme, name)
        assert value.startswith("#") and len(value) == 7


def test_switch_platform_color_is_joycon_red():
    assert ui_theme.PLATFORM_COLORS["switch"] == "#ff3c28"


def test_tile_and_pill_tokens_are_gone():
    # The quiet list intentionally drops the tile/star/ring vocabulary.
    for name in ("TILE_WIDTH", "TILE_HEIGHT", "TILE_RADIUS", "TILE_GAP", "TILE_BAR_HEIGHT"):
        assert not hasattr(ui_theme, name)
    assert "star" not in ui_theme.SWITCH
    assert "ring" not in ui_theme.SWITCH


# --- color conversion helpers -----------------------------------------------


def test_hex_to_rgb_and_rgb_to_hex_roundtrip():
    assert ui_theme.hex_to_rgb("#0a84ff") == (10, 132, 255)
    assert ui_theme.rgb_to_hex((10, 132, 255)) == "#0a84ff"
    assert ui_theme.rgb_to_hex(ui_theme.hex_to_rgb("#ff3c28")) == "#ff3c28"


def test_rgb_to_hex_clamps_out_of_range_channels():
    assert ui_theme.rgb_to_hex((-5, 300, 128)) == "#00ff80"


def test_lighten_darken_move_toward_white_and_black():
    assert ui_theme.lighten("#000000", 1.0) == "#ffffff"
    assert ui_theme.darken("#ffffff", 1.0) == "#000000"
    assert ui_theme.lighten("#000000", 0.0) == "#000000"
    assert ui_theme.darken("#ffffff", 0.0) == "#ffffff"
    assert ui_theme.lighten("#0a84ff", 0.5) != "#0a84ff"


# --- mix --------------------------------------------------------------------


def test_mix_endpoints_and_midpoint():
    assert ui_theme.mix("#000000", "#ffffff", 0.0) == "#000000"
    assert ui_theme.mix("#000000", "#ffffff", 1.0) == "#ffffff"
    assert ui_theme.mix("#000000", "#ffffff", 0.5) == "#808080"
    assert ui_theme.mix("#0a84ff", "#0a84ff", 0.3) == "#0a84ff"


def test_mix_clamps_and_supports_short_hex():
    assert ui_theme.mix("#000000", "#ffffff", -1) == "#000000"
    assert ui_theme.mix("#000000", "#ffffff", 5) == "#ffffff"
    assert ui_theme.mix("#fff", "#000", 0.5) == "#808080"


def test_mix_rejects_invalid_color():
    with pytest.raises(ValueError):
        ui_theme.mix("not-a-color", "#000000", 0.5)
    with pytest.raises(ValueError):
        ui_theme.mix("#zzzzzz", "#000000", 0.5)


# --- status labels ----------------------------------------------------------


def test_status_labels_cover_known_states_and_degrade():
    assert ui_theme.status_label("new") == "新"
    assert ui_theme.status_label("changed") == "有变化"
    assert ui_theme.status_label("unchanged") == "已备份"
    assert ui_theme.status_label("mystery") == "mystery"
    assert ui_theme.status_label(None) == "新"


def test_status_label_accepts_status_object():
    class _Status:
        status = "changed"

    assert ui_theme.status_label(_Status()) == "有变化"


# --- list row metrics -------------------------------------------------------


def test_row_metrics_are_defined():
    assert ui_theme.ROW_HEIGHT >= 36
    assert 0 < ui_theme.ROW_COVER < ui_theme.ROW_HEIGHT
    assert 0 < ui_theme.ROW_COVER_RADIUS <= ui_theme.ROW_COVER // 2
    assert ui_theme.ROW_PIP_WIDTH == 3


# --- save_row ---------------------------------------------------------------


def test_save_row_from_entry_and_status_string():
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="Monster Hunter Portable 3rd",
        path="/tmp/vol/PSP/SAVEDATA/ULJM05800",
        title_id="ULJM05800",
        slot="1",
    )
    row = ui_theme.save_row(entry, "new", starred=True)
    assert row["title"] == "Monster Hunter Portable 3rd"
    assert row["accent"] == ui_theme.PLATFORM_COLORS["psp"]
    assert row["platform"] == "psp"
    assert row["status"] == "new"
    assert row["status_label"] == "新"
    assert row["starred"] is True
    assert row["subtitle"] == "ULJM05800 · 1"


def test_save_row_accepts_status_object():
    class _Status:
        status = "changed"

    entry = SaveEntry(platform="vita", source_id="vita", display_name="Persona 4 Golden", path="/tmp/x")
    row = ui_theme.save_row(entry, _Status())
    assert row["status"] == "changed"
    assert row["status_label"] == "有变化"
    assert row["accent"] == ui_theme.PLATFORM_COLORS["vita"]
    assert row["subtitle"] == ""
    assert row["starred"] is False


def test_save_row_unknown_platform_and_missing_name():
    entry = SaveEntry(platform="mystery", source_id="x", display_name="", path="/tmp/y")
    row = ui_theme.save_row(entry, "new")
    assert row["accent"] == ui_theme.SWITCH["accent"]
    assert row["title"] == "/tmp/y"
    # No pastel face, monogram or pill survives on the quiet row.
    for stale in ("face", "monogram", "pill"):
        assert stale not in row

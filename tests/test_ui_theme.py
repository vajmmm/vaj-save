"""Unit tests for the pure helpers in ``vajsave.ui_theme``.

These cover the Switch "Basic White" token set plus the geometry/monogram/status
helpers that the Canvas tile grid relies on. Everything here is display-free so
it runs anywhere, even without a Tk display.
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
    assert tokens["ring"] == "#00a2ff"
    # No leftover Console Dark surfaces may remain in the main palette.
    assert {"#1c1c1e", "#2c2c2e", "#3a3a3c"}.isdisjoint(set(tokens.values()))


def test_platform_colors_are_canonical_and_hex():
    for key in ("all", "psp", "vita", "switch", "3ds", "nds", "gba"):
        value = ui_theme.PLATFORM_COLORS[key]
        assert value.startswith("#") and len(value) == 7


def test_switch_functional_tokens_match_spec():
    tokens = ui_theme.SWITCH
    assert tokens["muted"] == "#8b8b8b"
    assert tokens["success"] == "#30d158"
    assert tokens["warning"] == "#ff9f0a"
    assert tokens["danger"] == "#ff453a"
    assert tokens["ring"] == "#00a2ff"
    # The completed Basic White spec adds these interaction tokens.
    for key in ("hover", "line_strong", "star", "accent_hover", "ring_tint"):
        value = tokens[key]
        assert value.startswith("#") and len(value) == 7


def test_switch_module_level_tokens_exist():
    for name in ("HOVER", "LINE_STRONG", "STAR", "ACCENT_HOVER", "RING_TINT"):
        value = getattr(ui_theme, name)
        assert value.startswith("#") and len(value) == 7


def test_switch_platform_color_is_joycon_red():
    assert ui_theme.PLATFORM_COLORS["switch"] == "#ff3c28"


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


def test_ring_size_grows_the_tile():
    width, height = ui_theme.ring_size(246, 246)
    assert width > 246 and height > 246
    # The helper also accepts a box tuple or a single square dimension.
    assert ui_theme.ring_size((246, 246)) == (width, height)
    assert ui_theme.ring_size(246) == (width, width)


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


# --- grid_columns -----------------------------------------------------------


def test_grid_columns_fits_and_never_zeroes():
    assert ui_theme.grid_columns(640) == 3
    assert ui_theme.grid_columns(2000) == 11
    assert ui_theme.grid_columns(156) == 1
    assert ui_theme.grid_columns(326) == 2
    assert ui_theme.grid_columns(0) == 1
    assert ui_theme.grid_columns(-50) == 1
    assert ui_theme.grid_columns(None) == 1


# --- monogram ---------------------------------------------------------------


def test_monogram_ascii_and_cjk():
    assert ui_theme.monogram("Monster Hunter Portable 3rd") == "MH"
    assert ui_theme.monogram("Persona 4 Golden") == "P4"
    assert ui_theme.monogram("zelda") == "Z"
    assert ui_theme.monogram("塞尔达传说") == "塞"


def test_monogram_empty_falls_back_to_placeholder():
    assert ui_theme.monogram("") == "?"
    assert ui_theme.monogram("   ") == "?"
    assert ui_theme.monogram("!!!") == "?"
    assert ui_theme.monogram(None) == "?"


# --- status_pill ------------------------------------------------------------


def test_status_pill_known_statuses():
    new = ui_theme.status_pill("new")
    assert new["label"] == "新"
    assert new["fg"] == ui_theme.SWITCH["accent"]
    assert new["bg"] == ui_theme.mix(ui_theme.SWITCH["accent"], ui_theme.SWITCH["card"], 0.86)

    assert ui_theme.status_pill("changed")["label"] == "有变化"
    assert ui_theme.status_pill("changed")["fg"] == ui_theme.SWITCH["warning"]

    unchanged = ui_theme.status_pill("unchanged")
    assert unchanged["label"] == "已备份"
    assert unchanged["fg"] == ui_theme.SWITCH["success"]


def test_status_pill_unknown_degrades_gracefully():
    weird = ui_theme.status_pill("mystery")
    assert weird["label"] == "mystery"
    assert weird["fg"] == ui_theme.SWITCH["muted"]
    assert weird["bg"] == ui_theme.SWITCH["surface_alt"]

    assert ui_theme.status_pill(None)["label"] == "未知"


# --- tile_face --------------------------------------------------------------


def test_tile_face_from_entry_and_status_string():
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="Monster Hunter Portable 3rd",
        path="/tmp/vol/PSP/SAVEDATA/ULJM05800",
        title_id="ULJM05800",
        slot="1",
    )
    face = ui_theme.tile_face(entry, "new", starred=True)
    assert face["title"] == "Monster Hunter Portable 3rd"
    assert face["monogram"] == "MH"
    assert face["accent"] == ui_theme.PLATFORM_COLORS["psp"]
    assert face["platform"] == "psp"
    assert face["status"] == "new"
    assert face["starred"] is True
    assert face["pill"]["label"] == "新"
    assert face["subtitle"] == "ULJM05800 · 1"


def test_tile_face_accepts_status_object():
    class _Status:
        status = "changed"

    entry = SaveEntry(platform="vita", source_id="vita", display_name="Persona 4 Golden", path="/tmp/x")
    face = ui_theme.tile_face(entry, _Status())
    assert face["status"] == "changed"
    assert face["pill"]["label"] == "有变化"
    assert face["accent"] == ui_theme.PLATFORM_COLORS["vita"]
    assert face["subtitle"] == ""
    assert face["starred"] is False


def test_tile_face_unknown_platform_and_missing_name():
    entry = SaveEntry(platform="mystery", source_id="x", display_name="", path="/tmp/y")
    face = ui_theme.tile_face(entry, "new")
    assert face["accent"] == ui_theme.SWITCH["accent"]
    assert face["title"] == "/tmp/y"
    assert face["monogram"] == "?"


def test_tile_face_exposes_pastel_face_color():
    entry = SaveEntry(platform="switch", source_id="switch", display_name="Zelda", path="/tmp/z")
    face = ui_theme.tile_face(entry, "new")
    pastel = face["face"]
    assert pastel.startswith("#") and len(pastel) == 7
    # A pastel tint of the platform colour, never a plain white card.
    assert pastel != ui_theme.SWITCH["card"]
    assert pastel == ui_theme.mix(ui_theme.PLATFORM_COLORS["switch"], ui_theme.SWITCH["card"], 0.86)

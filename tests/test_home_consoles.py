"""Tests for home consoles (PS3, PS4, Wii U, Wii, Xbox 360) and view separation."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Dict

import pytest

from vajsave.app_state import AppState
from vajsave.artwork.providers import LIBRETRO_SYSTEM_NAMES
from vajsave.ftp_fetch import pull_preset
from vajsave.models import SaveEntry, VolumeInfo
from vajsave.platforms.catalog import (
    CONSOLE_PLATFORMS,
    HANDHELD_PLATFORMS,
    PLATFORM_CATEGORIES,
    PLATFORM_LABELS,
    PLATFORM_ORDER,
)
from vajsave.platforms.ps3 import scan_ps3
from vajsave.platforms.ps4 import scan_ps4
from vajsave.platforms.wii import scan_wii
from vajsave.platforms.wiiu import scan_wiiu
from vajsave.platforms.x360 import _read_stfs_metadata, scan_x360
from vajsave.qt_dialogs import PRESET_HINTS
from vajsave.qt_ui import CategorySwitcher, PlatformDock, VajSaveWindow
from vajsave.remote_ftp import (
    PS3_PRESET_KEY,
    PS4_PRESET_KEY,
    WII_PRESET_KEY,
    WIIU_PRESET_KEY,
    X360_PRESET_KEY,
    get_preset,
    presets,
)
from vajsave.scanner import guess_platform, scan
from vajsave.ui_theme import GALLERY_CASE_ASPECT, GALLERY_CASE_SCALE, PLATFORM_COLORS
from vajsave.volume import FakeVolumeProvider

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from conftest import FakeRemoteFtpClient, build_sfo, fake_client_factory


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def qt_state(tmp_path: Path):
    return AppState(library_root=tmp_path / "library")


def test_catalog_and_theme_home_consoles():
    """Verify home console catalog definitions, colors, case aspect and libretro names."""
    expected_consoles = ["ps3", "ps4", "wiiu", "wii", "x360"]
    assert CONSOLE_PLATFORMS == expected_consoles
    for p in expected_consoles:
        assert p in PLATFORM_ORDER
        assert p in PLATFORM_LABELS
        assert p in PLATFORM_COLORS
        assert p in GALLERY_CASE_ASPECT
        assert p in GALLERY_CASE_SCALE
        assert p in LIBRETRO_SYSTEM_NAMES

    assert PLATFORM_CATEGORIES["handheld"] == HANDHELD_PLATFORMS
    assert PLATFORM_CATEGORIES["console"] == CONSOLE_PLATFORMS


def test_ps3_scan_and_guess(tmp_path: Path):
    """Test PS3 SaveData scanning (SFO + PARAM.PFD) and shallow guess."""
    root = tmp_path / "ps3_device"
    save_dir = root / "PS3" / "SAVEDATA" / "BLUS30123-AUTO-"
    save_dir.mkdir(parents=True)
    sfo_bytes = build_sfo({"TITLE": "Demon's Souls", "TITLE_ID": "BLUS30123"})
    (save_dir / "PARAM.SFO").write_bytes(sfo_bytes)
    (save_dir / "PARAM.PFD").write_bytes(b"\x00" * 64)
    (save_dir / "ICON0.PNG").write_bytes(b"\x89PNG\r\n\x1a\n")

    assert guess_platform(root) == "ps3"

    result = scan(root)
    assert result.platform == "ps3"
    assert len(result.saves) == 1
    save = result.saves[0]
    assert save.platform == "ps3"
    assert save.title_id == "BLUS30123"
    assert save.display_name == "Demon's Souls"
    assert save.cover_path is not None
    assert Path(save.cover_path).name == "ICON0.PNG"


def test_ps4_scan_and_guess(tmp_path: Path):
    """Test PS4 CUSA save scanning with sce_sys/param.sfo."""
    root = tmp_path / "ps4_device"
    save_dir = root / "user" / "home" / "00000001" / "savedata" / "CUSA00123"
    sce_sys = save_dir / "sce_sys"
    sce_sys.mkdir(parents=True)
    sfo_bytes = build_sfo({"TITLE": "Bloodborne", "TITLE_ID": "CUSA00123"})
    (sce_sys / "param.sfo").write_bytes(sfo_bytes)
    (save_dir / "savedata00.bin").write_bytes(b"SAVE_PAYLOAD")

    assert guess_platform(root) == "ps4"

    result = scan(root)
    assert result.platform == "ps4"
    assert len(result.saves) == 1
    save = result.saves[0]
    assert save.platform == "ps4"
    assert save.title_id == "CUSA00123"
    assert save.display_name == "Bloodborne"


def test_wiiu_scan_and_guess(tmp_path: Path):
    """Test Wii U MLC save scanning with meta.xml parsing."""
    root = tmp_path / "wiiu_device"
    game_dir = root / "storage_mlc" / "usr" / "save" / "00050000" / "1010ec00"
    meta_dir = game_dir / "meta"
    meta_dir.mkdir(parents=True)
    meta_xml = """<?xml version="1.0" encoding="utf-8"?>
    <meta>
        <longname_zh>塞尔达传说 旷野之息</longname_zh>
        <longname_en>The Legend of Zelda: Breath of the Wild</longname_en>
        <product_code>WUP-P-ALZE</product_code>
        <title_id>000500001010EC00</title_id>
    </meta>
    """
    (meta_dir / "meta.xml").write_text(meta_xml, encoding="utf-8")
    (game_dir / "user" / "80000001").mkdir(parents=True)
    (game_dir / "user" / "80000001" / "game_data.bin").write_bytes(b"DATA")

    assert guess_platform(root) == "wiiu"

    result = scan(root)
    assert result.platform == "wiiu"
    assert len(result.saves) == 1
    save = result.saves[0]
    assert save.platform == "wiiu"
    assert save.title_id == "WUP-P-ALZE"
    assert save.display_name == "塞尔达传说 旷野之息"


def test_wii_scan_and_guess(tmp_path: Path):
    """Test Wii SaveGame Manager GX savegames scanning with title.txt."""
    root = tmp_path / "wii_device"
    save_dir = root / "savegames" / "RMCE01"
    save_dir.mkdir(parents=True)
    (save_dir / "title.txt").write_text("Mario Kart Wii\nExtra description", encoding="utf-8")
    (save_dir / "banner.bin").write_bytes(b"WII_BANNER")
    (save_dir / "data.bin").write_bytes(b"WII_SAVE")

    assert guess_platform(root) == "wii"

    result = scan(root)
    assert result.platform == "wii"
    assert len(result.saves) == 1
    save = result.saves[0]
    assert save.platform == "wii"
    assert save.title_id == "RMCE01"
    assert save.display_name == "Mario Kart Wii"


def _build_fake_stfs(title: str, display_name: str) -> bytes:
    """Construct minimal valid STFS container header."""
    buf = bytearray(0x500)
    buf[:4] = b"CON "
    # Offset 0x360: Title Name (128 bytes, UTF-16-BE)
    title_bytes = title.encode("utf-16-be")
    buf[0x360 : 0x360 + len(title_bytes)] = title_bytes
    # Offset 0x3E0: Display Name (128 bytes, UTF-16-BE)
    disp_bytes = display_name.encode("utf-16-be")
    buf[0x3E0 : 0x3E0 + len(disp_bytes)] = disp_bytes
    return bytes(buf)


def test_x360_scan_and_guess(tmp_path: Path):
    """Test Xbox 360 Content/<ProfileID>/<TitleID>/00000001 STFS scanning."""
    root = tmp_path / "x360_device"
    save_dir = root / "Content" / "E000005C8A7B2F10" / "4D5307E6" / "00000001"
    save_dir.mkdir(parents=True)
    stfs_data = _build_fake_stfs("Halo 3", "AutoSave 01")
    (save_dir / "halo3.sav").write_bytes(stfs_data)

    assert guess_platform(root) == "x360"

    result = scan(root)
    assert result.platform == "x360"
    assert len(result.saves) == 1
    save = result.saves[0]
    assert save.platform == "x360"
    assert save.title_id == "4D5307E6"
    assert save.display_name == "Halo 3"


def test_remote_console_ftp_presets():
    """Verify FTP presets and hint texts for PS3, PS4, Wii U, Wii, Xbox 360."""
    p_map = {p.key: p for p in presets()}
    assert PS3_PRESET_KEY in p_map
    assert PS4_PRESET_KEY in p_map
    assert WIIU_PRESET_KEY in p_map
    assert WII_PRESET_KEY in p_map
    assert X360_PRESET_KEY in p_map

    assert p_map[PS3_PRESET_KEY].port == 21
    assert p_map[PS4_PRESET_KEY].port == 2121
    assert p_map[WIIU_PRESET_KEY].port == 21
    assert p_map[WII_PRESET_KEY].port == 21
    assert p_map[X360_PRESET_KEY].port == 21

    for k in (PS3_PRESET_KEY, PS4_PRESET_KEY, WIIU_PRESET_KEY, WII_PRESET_KEY, X360_PRESET_KEY):
        assert k in PRESET_HINTS
        assert len(PRESET_HINTS[k]) > 0


def test_category_view_separation(tmp_path: Path):
    """Verify category view switching separates Handhelds and Consoles."""
    state = AppState(library_root=tmp_path / "library")

    # Mixed entries containing both handheld and console saves
    saves = [
        SaveEntry(platform="switch", source_id="switch", title_id="010000000001", display_name="Zelda BotW (Switch)", path=str(tmp_path / "s1")),
        SaveEntry(platform="vita", source_id="vita", title_id="PCSB00123", display_name="Persona 4 Golden", path=str(tmp_path / "s2")),
        SaveEntry(platform="ps4", source_id="ps4", title_id="CUSA00123", display_name="Bloodborne", path=str(tmp_path / "s3")),
        SaveEntry(platform="x360", source_id="x360", title_id="4D5307E6", display_name="Halo 3", path=str(tmp_path / "s4")),
        SaveEntry(platform="wii", source_id="wii", title_id="RMCE01", display_name="Mario Kart Wii", path=str(tmp_path / "s5")),
    ]
    # Set mock scanned result
    from vajsave.models import ScanResult
    state.current_result = ScanResult(root_path=str(tmp_path), platform="mixed", saves=saves)

    # 1. Default or 'all' category shows all saves
    state.selected_category = "all"
    state.selected_platform = "all"
    assert len(state.visible_saves()) == 5

    # 2. Handheld category view filters strictly to handhelds
    state.set_category("handheld")
    assert state.selected_category == "handheld"
    visible = state.visible_saves()
    assert len(visible) == 2
    assert {s.platform for s in visible} == {"switch", "vita"}

    # Selecting a specific handheld platform
    state.set_platform_filter("switch")
    assert len(state.visible_saves()) == 1
    assert state.visible_saves()[0].platform == "switch"

    # 3. Console category view filters strictly to consoles
    state.set_category("console")
    assert state.selected_category == "console"
    assert state.selected_platform == "all"
    visible_consoles = state.visible_saves()
    assert len(visible_consoles) == 3
    assert {s.platform for s in visible_consoles} == {"ps4", "x360", "wii"}

    # Selecting a specific console platform
    state.set_platform_filter("ps4")
    assert len(state.visible_saves()) == 1
    assert state.visible_saves()[0].platform == "ps4"


def test_dock_category_switcher_ui(qt_app, qt_state):
    """Test PlatformDock CategorySwitcher toggles buttons and auto-selects."""
    window = VajSaveWindow(qt_state)
    try:
        dock = window.dock
        # Initially in handheld view
        assert dock.current_category == "handheld"
        for p in HANDHELD_PLATFORMS:
            assert not dock.buttons[p].isHidden()
        for p in CONSOLE_PLATFORMS:
            assert dock.buttons[p].isHidden()

        # Switch to console view via switcher
        dock.category_switcher.btn_console.click()
        assert dock.current_category == "console"
        for p in CONSOLE_PLATFORMS:
            assert not dock.buttons[p].isHidden()
        for p in HANDHELD_PLATFORMS:
            assert dock.buttons[p].isHidden()
        assert dock.buttons["all"].isChecked()

        # Switch back to handheld view
        dock.category_switcher.btn_handheld.click()
        assert dock.current_category == "handheld"
        for p in HANDHELD_PLATFORMS:
            assert not dock.buttons[p].isHidden()
        for p in CONSOLE_PLATFORMS:
            assert dock.buttons[p].isHidden()

        # set_current for a console auto-switches category to console
        dock.set_current("ps4")
        assert dock.current_category == "console"
        assert dock.buttons["ps4"].isChecked()
        assert not dock.buttons["ps4"].isHidden()
        assert dock.buttons["vita"].isHidden()
    finally:
        window.close()

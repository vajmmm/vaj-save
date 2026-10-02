"""Unit tests for homebrew and system utility filtering across all platforms."""

from __future__ import annotations

from pathlib import Path
import pytest

from vajsave.homebrew import (
    filter_homebrew_saves,
    is_homebrew_or_tool,
)
from vajsave.models import SaveEntry
from vajsave.scanner import scan


def test_is_homebrew_vita_by_title_id():
    assert is_homebrew_or_tool("vita", title_id="ADRBUBMAN") is True
    assert is_homebrew_or_tool("vita", title_id="MAIM00001") is True
    assert is_homebrew_or_tool("vita", title_id="DUSKGXM01") is True
    assert is_homebrew_or_tool("vita", title_id="VITASHELL") is True
    assert is_homebrew_or_tool("vita", title_id="TOOLBOX00") is True
    assert is_homebrew_or_tool("vita", title_id="TOOLBOX20") is True
    assert is_homebrew_or_tool("vita", title_id="WILIWILI0") is True
    assert is_homebrew_or_tool("vita", title_id="SKGD3PL0Y") is True
    assert is_homebrew_or_tool("vita", title_id="AUTOPLUG2") is True
    assert is_homebrew_or_tool("vita", title_id="SAVEMGR00") is True

    # Real games must not be filtered
    assert is_homebrew_or_tool("vita", title_id="PCSD00071") is False
    assert is_homebrew_or_tool("vita", title_id="PCSH00021") is False
    assert is_homebrew_or_tool("vita", title_id="PCSE00507") is False
    assert is_homebrew_or_tool("vita", title_id="PCSB00560") is False


def test_is_homebrew_psp_by_title_id_and_folder():
    assert is_homebrew_or_tool("psp", title_id="FASTRECOVERY") is True
    assert is_homebrew_or_tool("psp", title_id="PROUPDATE") is True
    assert is_homebrew_or_tool("psp", title_id="MEUPDATE") is True
    assert is_homebrew_or_tool("psp", title_id="CWCHEAT") is True
    assert is_homebrew_or_tool("psp", title_id="ARK4") is True
    assert is_homebrew_or_tool("psp", path="/PSP/SAVEDATA/FASTRECOVERY") is True

    # Real PSP games must not be filtered
    assert is_homebrew_or_tool("psp", title_id="ULUS10466") is False
    assert is_homebrew_or_tool("psp", title_id="ULJM05800") is False
    assert is_homebrew_or_tool("psp", title_id="NPJH50107") is False


def test_is_homebrew_3ds_by_title_id():
    assert is_homebrew_or_tool("3ds", title_id="00040000000F8000") is True
    assert is_homebrew_or_tool("3ds", title_id="0x00F80") is True
    assert is_homebrew_or_tool("3ds", title_id="0004000000177700") is True
    assert is_homebrew_or_tool("3ds", title_id="0x01777") is True
    assert is_homebrew_or_tool("3ds", title_id="0004000000021400") is True
    assert is_homebrew_or_tool("3ds", title_id="0x00214") is True
    assert is_homebrew_or_tool("3ds", title_id="0004000000190100") is True

    # Real 3DS games must not be filtered
    assert is_homebrew_or_tool("3ds", title_id="00040000001B5000") is False
    assert is_homebrew_or_tool("3ds", title_id="0x00EC3") is False


def test_is_homebrew_switch_by_title_id():
    assert is_homebrew_or_tool("switch", title_id="054E464558540000") is True  # DBI
    assert is_homebrew_or_tool("switch", title_id="0500000000000000") is True  # JKSV
    assert is_homebrew_or_tool("switch", title_id="010000000000100D") is True  # hbmenu
    assert is_homebrew_or_tool("switch", title_id="0500000000000002") is True  # Tinfoil

    # Real Switch games must not be filtered
    assert is_homebrew_or_tool("switch", title_id="0100000000010000") is False


def test_is_homebrew_by_display_name_patterns():
    # Tools shown in user's real scan screenshot
    assert is_homebrew_or_tool("vita", display_name="Adrenaline Bubbles Manager") is True
    assert is_homebrew_or_tool("vita", display_name="[ufo汉化]MaiDumpTool中文版") is True
    assert is_homebrew_or_tool("vita", display_name="dusklight Vita") is True
    assert is_homebrew_or_tool("vita", display_name="VitaShell") is True
    assert is_homebrew_or_tool("vita", display_name="VITA工具箱") is True
    assert is_homebrew_or_tool("vita", display_name="TOOLBOX2") is True
    assert is_homebrew_or_tool("vita", display_name="wiliwili") is True

    # Other common tools across platforms
    assert is_homebrew_or_tool("switch", display_name="DBI Installer") is True
    assert is_homebrew_or_tool("3ds", display_name="FBI") is True
    assert is_homebrew_or_tool("3ds", display_name="Anemone3DS") is True
    assert is_homebrew_or_tool("3ds", display_name="Checkpoint") is True
    assert is_homebrew_or_tool("psp", display_name="6.60 PRO-C Fast Recovery") is True

    # Genuine games must never match
    assert is_homebrew_or_tool("psp", display_name="Dragon Ball Z 真武道会2") is False
    assert is_homebrew_or_tool("psp", display_name="Grand Knights History") is False
    assert is_homebrew_or_tool("psp", display_name="Tekken 6") is False
    assert is_homebrew_or_tool("vita", display_name="Child of Light") is False
    assert is_homebrew_or_tool("vita", display_name="Cuphead") is False
    assert is_homebrew_or_tool("vita", display_name="Gravity Rush") is False
    assert is_homebrew_or_tool("vita", display_name="Killzone Mercenary") is False
    assert is_homebrew_or_tool("vita", display_name="Persona 4 Golden") is False


def test_filter_homebrew_saves():
    saves = [
        SaveEntry(platform="vita", source_id="vita", display_name="Killzone", title_id="PCSD00071", path="/vita/k"),
        SaveEntry(platform="vita", source_id="vita", display_name="Adrenaline Bubbles Manager", title_id="ADRBUBMAN", path="/vita/a"),
        SaveEntry(platform="vita", source_id="vita", display_name="[ufo汉化]MaiDumpTool中文版", title_id="MAIM00001", path="/vita/m"),
        SaveEntry(platform="vita", source_id="vita", display_name="Persona 4 Golden", title_id="PCSH00021", path="/vita/p"),
        SaveEntry(platform="vita", source_id="vita", display_name="VitaShell", title_id="VITASHELL", path="/vita/v"),
        SaveEntry(platform="psp", source_id="psp", display_name="Tekken 6", title_id="ULUS10466", path="/psp/t"),
        SaveEntry(platform="psp", source_id="psp", display_name="Fast Recovery", title_id="FASTRECOVERY", path="/psp/f"),
    ]
    filtered = filter_homebrew_saves(saves)
    assert len(filtered) == 3
    remaining_names = [s.display_name for s in filtered]
    assert remaining_names == ["Killzone", "Persona 4 Golden", "Tekken 6"]


def test_scanner_filters_homebrew_from_results(tmp_path: Path):
    # Setup Vita savedata directory with real game and homebrew tools
    vita_savedata = tmp_path / "user" / "00" / "savedata"
    (vita_savedata / "PCSD00071").mkdir(parents=True)
    (vita_savedata / "PCSD00071" / "save.bin").write_bytes(b"killzone")

    (vita_savedata / "ADRBUBMAN").mkdir(parents=True)
    (vita_savedata / "ADRBUBMAN" / "config.bin").write_bytes(b"bubbles")

    (vita_savedata / "MAIM00001").mkdir(parents=True)
    (vita_savedata / "MAIM00001" / "config.bin").write_bytes(b"maidump")

    result = scan(tmp_path)
    assert len(result.saves) == 1
    assert result.saves[0].title_id == "PCSD00071"

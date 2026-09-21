from pathlib import Path

from vajsave.platforms.catalog import PLATFORM_LABELS, PLATFORM_ORDER
from vajsave.rom_formats import ROM_EXTENSIONS
from vajsave.scanner import _detected_platforms, guess_platform, scan
from vajsave.ui_theme import PLATFORM_COLORS


def test_gb_is_registered_on_the_dock():
    assert PLATFORM_ORDER == [
        "all",
        "psp",
        "vita",
        "switch",
        "3ds",
        "nds",
        "gb",
        "gbc",
        "gba",
    ]
    assert PLATFORM_LABELS["gb"] == "GB"
    assert PLATFORM_ORDER.index("nds") < PLATFORM_ORDER.index("gb")
    assert PLATFORM_ORDER.index("gb") < PLATFORM_ORDER.index("gbc")
    assert PLATFORM_ORDER.index("gbc") < PLATFORM_ORDER.index("gba")
    assert ROM_EXTENSIONS["gb"] == (".gb",)
    assert PLATFORM_COLORS["gb"] == "#9aa56a"


def test_gb_roms_dir_sibling_sav(tmp_path: Path):
    rom_dir = tmp_path / "roms" / "gb"
    rom_dir.mkdir(parents=True)
    (rom_dir / "Tetris.gb").write_bytes(b"rom")
    (rom_dir / "Tetris.sav").write_bytes(b"sav")
    (rom_dir / "orphan.sav").write_bytes(b"no_rom")

    result = scan(tmp_path)
    assert result.platform == "gb"
    names = {s.display_name for s in result.saves}
    assert names == {"Tetris"}
    assert result.saves[0].platform == "gb"
    assert result.saves[0].source_id == "gb_r4"


def test_gb_saves_subdir_next_to_rom(tmp_path: Path):
    rom_dir = tmp_path / "roms" / "gb"
    rom_dir.mkdir(parents=True)
    (rom_dir / "Kirby.gb").write_bytes(b"rom")
    saves = rom_dir / "saves"
    saves.mkdir()
    (saves / "Kirby.sav").write_bytes(b"ok")
    (saves / "Kirby.bak").write_bytes(b"skip")

    result = scan(tmp_path)
    assert len(result.saves) == 1
    assert result.saves[0].platform == "gb"
    assert result.saves[0].display_name == "Kirby"
    assert result.saves[0].source_id == "gb_saves"


def test_gb_srm_sibling_is_recognised(tmp_path: Path):
    folder = tmp_path / "cart"
    folder.mkdir()
    (folder / "Zelda.gb").write_bytes(b"rom")
    (folder / "Zelda.srm").write_bytes(b"sav")

    result = scan(folder)
    assert result.platform == "gb"
    assert [s.display_name for s in result.saves] == ["Zelda"]


def test_guess_platform_and_detected_see_roms_gb(tmp_path: Path):
    (tmp_path / "roms" / "gb").mkdir(parents=True)
    assert guess_platform(tmp_path) == "gb"
    assert "gb" in _detected_platforms(tmp_path)


def test_guess_platform_everdrive_gb(tmp_path: Path):
    (tmp_path / "EDGB").mkdir()
    assert guess_platform(tmp_path) == "gb"
    assert "gb" in _detected_platforms(tmp_path)


def test_saver_matching_gb_rom_is_classified_gb(tmp_path: Path):
    saver = tmp_path / "SAVER"
    saver.mkdir()
    (saver / "foo.sav").write_bytes(b"sav")
    (tmp_path / "roms" / "gb").mkdir(parents=True)
    (tmp_path / "roms" / "gb" / "foo.gb").write_bytes(b"rom")

    result = scan(tmp_path)
    gb_saves = [s for s in result.saves if s.platform == "gb"]
    gba_saves = [s for s in result.saves if s.platform == "gba"]
    assert [s.display_name for s in gb_saves] == ["foo"]
    assert gba_saves == []


def test_saver_without_rom_stays_gba(tmp_path: Path):
    saver = tmp_path / "SAVER"
    saver.mkdir()
    (saver / "Game.sav").write_bytes(b"x")

    result = scan(tmp_path)
    assert result.platform == "gba"
    assert len(result.saves) == 1
    assert result.saves[0].platform == "gba"
    assert result.saves[0].display_name == "Game"
    assert result.saves[0].source_id == "gba_ezflash"


def test_saver_matching_gba_rom_stays_gba(tmp_path: Path):
    saver = tmp_path / "SAVER"
    saver.mkdir()
    (saver / "Emerald.sav").write_bytes(b"sav")
    (tmp_path / "Emerald.gba").write_bytes(b"rom")

    result = scan(tmp_path)
    assert [s.platform for s in result.saves] == ["gba"]
    assert result.saves[0].display_name == "Emerald"

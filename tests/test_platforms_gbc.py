from pathlib import Path

from vajsave.platforms.catalog import PLATFORM_LABELS
from vajsave.rom_formats import ROM_EXTENSIONS
from vajsave.scanner import _detected_platforms, guess_platform, scan
from vajsave.ui_theme import PLATFORM_COLORS


def test_gbc_is_registered():
    assert PLATFORM_LABELS["gbc"] == "GBC"
    assert ROM_EXTENSIONS["gbc"] == (".gbc",)
    assert PLATFORM_COLORS["gbc"] == "#ff6b8a"


def test_gbc_roms_dir_sibling_sav(tmp_path: Path):
    rom_dir = tmp_path / "roms" / "gbc"
    rom_dir.mkdir(parents=True)
    (rom_dir / "Pokemon Gold.gbc").write_bytes(b"rom")
    (rom_dir / "Pokemon Gold.sav").write_bytes(b"sav")
    (rom_dir / "orphan.sav").write_bytes(b"no_rom")

    result = scan(tmp_path)
    assert result.platform == "gbc"
    names = {s.display_name for s in result.saves}
    assert names == {"Pokemon Gold"}
    assert result.saves[0].platform == "gbc"
    assert result.saves[0].source_id == "gbc_r4"


def test_gbc_saves_subdir_next_to_rom(tmp_path: Path):
    rom_dir = tmp_path / "roms" / "gbc"
    rom_dir.mkdir(parents=True)
    (rom_dir / "Crystal.gbc").write_bytes(b"rom")
    saves = rom_dir / "saves"
    saves.mkdir()
    (saves / "Crystal.sav").write_bytes(b"ok")

    result = scan(tmp_path)
    assert len(result.saves) == 1
    assert result.saves[0].platform == "gbc"
    assert result.saves[0].display_name == "Crystal"
    assert result.saves[0].source_id == "gbc_saves"


def test_guess_platform_and_detected_see_roms_gbc(tmp_path: Path):
    (tmp_path / "roms" / "gbc").mkdir(parents=True)
    assert guess_platform(tmp_path) == "gbc"
    assert "gbc" in _detected_platforms(tmp_path)


def test_saver_matching_gbc_rom_is_classified_gbc(tmp_path: Path):
    saver = tmp_path / "SAVER"
    saver.mkdir()
    (saver / "foo.sav").write_bytes(b"sav")
    (tmp_path / "foo.gbc").write_bytes(b"rom")

    result = scan(tmp_path)
    gbc_saves = [s for s in result.saves if s.platform == "gbc"]
    gba_saves = [s for s in result.saves if s.platform == "gba"]
    assert [s.display_name for s in gbc_saves] == ["foo"]
    assert gba_saves == []


def test_saver_prefers_gbc_over_gba_when_both_roms_exist(tmp_path: Path):
    saver = tmp_path / "SAVER"
    saver.mkdir()
    (saver / "Shared.sav").write_bytes(b"sav")
    (tmp_path / "Shared.gbc").write_bytes(b"gbc")
    (tmp_path / "Shared.gba").write_bytes(b"gba")

    result = scan(tmp_path)
    assert [s.platform for s in result.saves] == ["gbc"]
    assert result.saves[0].display_name == "Shared"

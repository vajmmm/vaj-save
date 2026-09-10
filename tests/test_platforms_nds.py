from pathlib import Path

from vajsave.scanner import scan


def test_nds_sibling_when_user_selects_rom_folder(tmp_path: Path):
    folder = tmp_path / "cart_dump"
    folder.mkdir()
    (folder / "Kirby.nds").write_bytes(b"rom")
    (folder / "Kirby.sav").write_bytes(b"sav")
    (folder / "orphan.sav").write_bytes(b"no_rom")

    result = scan(folder)
    assert result.platform == "nds"
    assert len(result.saves) == 1
    assert result.saves[0].display_name == "Kirby"
    assert result.saves[0].source_id == "nds_r4"


def test_nds_r4_dat_fingerprint(tmp_path: Path):
    (tmp_path / "R4.dat").write_text("marker")
    game = tmp_path / "ROMS"
    game.mkdir()
    (game / "DK.nds").write_bytes(b"rom")
    (game / "DK.sav").write_bytes(b"sav")

    result = scan(tmp_path)
    assert any(s.platform == "nds" for s in result.saves)
    assert any(s.display_name == "DK" for s in result.saves)


def test_nds_twilight_ignores_non_sav(tmp_path: Path):
    rom_dir = tmp_path / "roms" / "nds"
    rom_dir.mkdir(parents=True)
    (rom_dir / "Game.nds").write_bytes(b"rom")
    saves = rom_dir / "saves"
    saves.mkdir()
    (saves / "Game.sav").write_bytes(b"ok")
    (saves / "Game.bak").write_bytes(b"skip")

    result = scan(tmp_path)
    assert len(result.saves) == 1
    assert result.saves[0].source_id == "nds_twilight"

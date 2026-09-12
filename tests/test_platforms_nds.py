from pathlib import Path

import pytest

from vajsave.scanner import guess_platform, scan


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


# --- Wood R4: __rpg kernel fingerprint ------------------------------------


def test_nds_wood_rpg_fingerprint_layout(tmp_path: Path):
    (tmp_path / "__rpg").mkdir()
    games = tmp_path / "games"
    games.mkdir()
    (games / "Mario Kart DS.nds").write_bytes(b"rom")
    (games / "Mario Kart DS.sav").write_bytes(b"sav")

    result = scan(tmp_path)
    assert result.platform == "nds"
    assert any(s.display_name == "Mario Kart DS" for s in result.saves)
    assert guess_platform(tmp_path) == "nds"


# --- sibling depth: ROM/save on the 4th level below root ------------------


def test_nds_sibling_sav_on_fourth_level(tmp_path: Path):
    (tmp_path / "R4.dat").write_text("marker")
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (deep / "Zelda.nds").write_bytes(b"rom")
    (deep / "Zelda.sav").write_bytes(b"sav")

    result = scan(tmp_path)
    assert any(s.display_name == "Zelda" for s in result.saves)


def test_nds_sibling_walk_stays_bounded(tmp_path: Path):
    (tmp_path / "R4.dat").write_text("marker")
    too_deep = tmp_path / "a" / "b" / "c" / "d" / "e"
    too_deep.mkdir(parents=True)
    (too_deep / "Buried.nds").write_bytes(b"rom")
    (too_deep / "Buried.sav").write_bytes(b"sav")

    result = scan(tmp_path)
    assert not any(s.display_name == "Buried" for s in result.saves)


# --- top-level SAVE/saves separated from ROMs -----------------------------


def test_nds_top_level_save_dir_pairs_by_unique_stem(tmp_path: Path):
    (tmp_path / "R4.dat").write_text("marker")
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Pokemon Platinum.nds").write_bytes(b"rom")
    save_dir = tmp_path / "SAVE"
    save_dir.mkdir()
    (save_dir / "Pokemon Platinum.sav").write_bytes(b"sav")
    (save_dir / "orphan.sav").write_bytes(b"no_rom")
    (save_dir / "notes.txt").write_bytes(b"not_a_save")
    (save_dir / "nested").mkdir()

    result = scan(tmp_path)
    names = {s.display_name for s in result.saves}
    assert "Pokemon Platinum" in names
    assert "orphan" not in names


def test_nds_top_level_lowercase_saves_dir_pairs_by_unique_stem(tmp_path: Path):
    rom_dir = tmp_path / "games"
    rom_dir.mkdir()
    (rom_dir / "Advance Wars.nds").write_bytes(b"rom")
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    (save_dir / "Advance Wars.sav").write_bytes(b"sav")

    result = scan(tmp_path)
    names = {s.display_name for s in result.saves}
    assert "Advance Wars" in names


def test_nds_save_dir_without_matching_rom_is_ignored(tmp_path: Path):
    save_dir = tmp_path / "SAVE"
    save_dir.mkdir()
    (save_dir / "random.sav").write_bytes(b"x")

    result = scan(tmp_path)
    assert result.platform == "unknown"
    assert not any(s.platform == "nds" for s in result.saves)


def test_nds_save_dir_ambiguous_stem_is_ignored(tmp_path: Path):
    (tmp_path / "R4.dat").write_text("marker")
    first = tmp_path / "set1"
    second = tmp_path / "set2"
    first.mkdir()
    second.mkdir()
    (first / "Shared.nds").write_bytes(b"rom")
    (second / "Shared.nds").write_bytes(b"rom")
    save_dir = tmp_path / "SAVE"
    save_dir.mkdir()
    (save_dir / "Shared.sav").write_bytes(b"sav")

    result = scan(tmp_path)
    assert not any(s.display_name == "Shared" for s in result.saves)


def test_nds_save_dir_symlink_escape_is_ignored(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Game.sav").write_bytes(b"escaped")

    vol = tmp_path / "vol"
    rom_dir = vol / "games"
    rom_dir.mkdir(parents=True)
    (rom_dir / "Game.nds").write_bytes(b"rom")
    link = vol / "saves"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not supported on this platform/filesystem")

    result = scan(vol)
    assert not any(s.display_name == "Game" for s in result.saves)


def test_nds_rom_index_is_bounded_and_safe(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Outside.nds").write_bytes(b"rom")

    vol = tmp_path / "vol"
    save_dir = vol / "SAVE"
    save_dir.mkdir(parents=True)
    (vol / "Real.nds").write_bytes(b"rom")
    (save_dir / "Real.sav").write_bytes(b"sav")
    escape = vol / "escape"
    loop = vol / "loop"
    try:
        escape.symlink_to(outside, target_is_directory=True)
        loop.symlink_to(vol, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not supported on this platform/filesystem")

    # The bounded ROM index must terminate despite the cycle and must not walk
    # the symlink that escapes the root, so only the in-root pair is matched.
    result = scan(vol)
    assert any(s.display_name == "Real" for s in result.saves)
    assert not any(s.display_name == "Outside" for s in result.saves)

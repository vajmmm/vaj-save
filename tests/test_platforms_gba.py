from pathlib import Path

import pytest

from vajsave.scanner import scan


def test_gba_saver_selected_directly(tmp_path: Path):
    saver = tmp_path / "SAVER"
    saver.mkdir()
    (saver / "Game.sav").write_bytes(b"x")
    (saver / "ignore.bin").write_bytes(b"y")

    result = scan(saver)
    assert result.platform == "gba"
    assert len(result.saves) == 1
    assert result.saves[0].display_name == "Game"
    assert result.saves[0].source_id == "gba_ezflash"


def test_gba_everdrive_save_selected_directly(tmp_path: Path):
    save_dir = tmp_path / "GBASYS" / "SAVE"
    save_dir.mkdir(parents=True)
    (save_dir / "A.fla").write_bytes(b"a")
    (save_dir / "B.eep").write_bytes(b"b")

    result = scan(save_dir)
    assert result.platform == "gba"
    assert any(s.source_id == "gba_everdrive" for s in result.sources)
    names = {s.display_name for s in result.saves}
    assert names == {"A", "B"}


def test_gba_everdrive_pro_gamedata_selected_directly(tmp_path: Path):
    gd = tmp_path / "EDGBA" / "gamedata"
    game = gd / "Title"
    game.mkdir(parents=True)
    (game / "bram").write_bytes(b"raw")
    (game / "notes.txt").write_text("skip")
    empty = gd / "EmptyGame"
    empty.mkdir()

    result = scan(gd)
    assert result.platform == "gba"
    assert len(result.saves) == 1
    assert result.saves[0].display_name == "Title"
    assert result.saves[0].source_id == "gba_everdrive_pro"


def test_gba_everdrive_pro_wrapped_two_levels(tmp_path: Path):
    bram = tmp_path / "outer" / "inner" / "EDGBA" / "gamedata" / "Castlevania" / "bram.bin"
    bram.parent.mkdir(parents=True)
    bram.write_bytes(b"cv")

    result = scan(tmp_path)
    assert any(s.source_id == "gba_everdrive_pro" for s in result.sources)
    assert any(s.display_name == "Castlevania" for s in result.saves)


def test_gba_superchis_savegame(tmp_path: Path):
    savegame = tmp_path / "SAVEGAME"
    savegame.mkdir()
    (savegame / "Emerald.sav").write_bytes(b"e")
    (savegame / "Emerald.1.sav").write_bytes(b"e1")
    (savegame / "skip.txt").write_text("no")

    result = scan(tmp_path)
    assert result.platform == "gba"
    assert any(s.source_id == "gba_superchis" for s in result.sources)
    names = {s.display_name for s in result.saves}
    assert names == {"Emerald", "Emerald.1"}
    assert all(s.path.endswith(".sav") for s in result.saves)


def test_gba_superchis_saves_requires_superfw_fingerprint(tmp_path: Path):
    saves_dir = tmp_path / "SAVES"
    saves_dir.mkdir()
    (saves_dir / "Alone.sav").write_bytes(b"a")

    bare = scan(tmp_path)
    assert not any(s.platform == "gba" for s in bare.saves)

    (tmp_path / ".superfw").mkdir()
    marked = scan(tmp_path)
    assert any(s.source_id == "gba_superchis" for s in marked.sources)
    assert any(s.display_name == "Alone" for s in marked.saves)


def test_gba_superchis_saves_selected_directly(tmp_path: Path):
    saves_dir = tmp_path / "SAVES"
    saves_dir.mkdir()
    (saves_dir / "Direct.sav").write_bytes(b"d")

    result = scan(saves_dir)
    assert result.platform == "gba"
    assert result.saves[0].source_id == "gba_superchis"
    assert result.saves[0].display_name == "Direct"


# --- nested / categorised save subdirectories --------------------------------


def test_gba_ezflash_saver_nested_category_subdir_is_scanned(tmp_path: Path):
    saver = tmp_path / "SAVER"
    (saver / "RPG").mkdir(parents=True)
    (saver / "RPG" / "Golden Sun.sav").write_bytes(b"gs")

    result = scan(saver)
    assert result.platform == "gba"
    assert any(s.display_name == "Golden Sun" for s in result.saves)
    assert any(s.source_id == "gba_ezflash" for s in result.sources)


def test_gba_savegame_nested_game_folder_is_scanned(tmp_path: Path):
    saves_dir = tmp_path / "SAVEGAME"
    (saves_dir / "Action" / "Metroid Fusion").mkdir(parents=True)
    (saves_dir / "Action" / "Metroid Fusion" / "Metroid Fusion.sav").write_bytes(b"mf")
    (saves_dir / "Top.sav").write_bytes(b"top")

    result = scan(tmp_path)
    names = {s.display_name for s in result.saves}
    assert "Metroid Fusion" in names
    assert "Top" in names
    assert all(s.source_id == "gba_superchis" for s in result.saves)


def test_gba_everdrive_save_nested_subdir_is_scanned(tmp_path: Path):
    save_dir = tmp_path / "GBASYS" / "SAVE"
    (save_dir / "slot").mkdir(parents=True)
    (save_dir / "slot" / "Nested.sav").write_bytes(b"n")

    result = scan(save_dir)
    assert any(s.display_name == "Nested" for s in result.saves)
    assert any(s.source_id == "gba_everdrive" for s in result.sources)


def test_gba_nested_scan_does_not_follow_symlinked_dir(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Secret.sav").write_bytes(b"secret")

    saver = tmp_path / "vol" / "SAVER"
    saver.mkdir(parents=True)
    (saver / "Real.sav").write_bytes(b"real")
    link = saver / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not supported on this platform/filesystem")

    result = scan(tmp_path / "vol")
    assert result.platform == "gba"
    assert any(s.display_name == "Real" for s in result.saves)
    assert not any(s.display_name == "Secret" for s in result.saves)


def test_gba_nested_scan_does_not_escape_root(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Escaped.sav").write_bytes(b"escaped")

    vol = tmp_path / "vol"
    saves = vol / "SAVEGAME"
    saves.mkdir(parents=True)
    (saves / "Kept.sav").write_bytes(b"kept")
    escape = saves / "escape"
    try:
        escape.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not supported on this platform/filesystem")

    result = scan(vol)
    assert any(s.display_name == "Kept" for s in result.saves)
    assert not any(s.display_name == "Escaped" for s in result.saves)


def test_gba_nested_scan_depth_is_bounded(tmp_path: Path):
    saver = tmp_path / "SAVER"
    deep = saver
    for i in range(8):
        deep = deep / f"level{i}"
    deep.mkdir(parents=True)
    (deep / "Deep.sav").write_bytes(b"deep")
    (saver / "Shallow.sav").write_bytes(b"shallow")

    result = scan(saver)
    names = {s.display_name for s in result.saves}
    assert "Shallow" in names
    assert "Deep" not in names

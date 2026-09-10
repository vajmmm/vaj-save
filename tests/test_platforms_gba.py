from pathlib import Path

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

from pathlib import Path

from vajsave.scanner import scan


def test_3ds_jksm_extdata_and_syssave(tmp_path: Path):
    ext = tmp_path / "JKSV" / "ExtData" / "HomeMenu" / "slotA"
    ext.mkdir(parents=True)
    (ext / "data.bin").write_bytes(b"e")
    sys_slot = tmp_path / "JKSV" / "SysSave" / "Config" / "main"
    sys_slot.mkdir(parents=True)
    (sys_slot / "cfg.bin").write_bytes(b"s")

    result = scan(tmp_path)
    assert result.platform == "3ds"
    ids = {s.source_id for s in result.sources}
    assert ids == {"3ds_jksm"}
    names = {s.display_name for s in result.saves}
    assert names == {"HomeMenu", "Config"}
    assert not any(s.platform == "switch" for s in result.saves)


def test_3ds_jksm_game_without_slot(tmp_path: Path):
    game = tmp_path / "JKSV" / "Saves" / "DirectExport"
    game.mkdir(parents=True)
    (game / "save.bin").write_bytes(b"d")

    result = scan(tmp_path)
    assert len(result.saves) == 1
    assert result.saves[0].display_name == "DirectExport"
    assert result.saves[0].slot is None
    assert result.saves[0].source_id == "3ds_jksm"


def test_3ds_jksm_when_jksv_selected_directly(tmp_path: Path):
    jksv = tmp_path / "JKSV"
    slot = jksv / "Saves" / "GameX" / "Backup1"
    slot.mkdir(parents=True)
    (slot / "main").write_bytes(b"x")

    result = scan(jksv)
    assert result.platform == "3ds"
    assert any(s.source_id == "3ds_jksm" for s in result.sources)
    assert len(result.saves) == 1


def test_3ds_jksm_when_saves_category_selected(tmp_path: Path):
    saves = tmp_path / "JKSV" / "Saves"
    slot = saves / "GameY" / "s1"
    slot.mkdir(parents=True)
    (slot / "f").write_bytes(b"y")

    result = scan(saves)
    assert result.platform == "3ds"
    assert len(result.saves) == 1
    assert result.saves[0].display_name == "GameY"


def test_3ds_jksm_empty_category_not_source(tmp_path: Path):
    cat = tmp_path / "JKSV" / "Saves"
    cat.mkdir(parents=True)
    (cat / "readme.txt").write_text("empty")

    result = scan(tmp_path)
    assert not any(s.source_id == "3ds_jksm" for s in result.sources)

import os
import stat
from pathlib import Path
import pytest
from vajsave.scanner import scan, guess_platform
from conftest import build_sfo


def test_psp_save_with_sfo(tmp_path: Path, psp_sfo_bytes: bytes):
    save_dir = tmp_path / "PSP" / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    (save_dir / "MHP3RD.DAT").write_bytes(b"dummy_save_data")

    result = scan(tmp_path)
    assert result.platform == "psp"
    assert len(result.sources) == 1
    assert result.sources[0].source_id == "psp"
    assert len(result.saves) == 1
    entry = result.saves[0]
    assert entry.platform == "psp"
    assert entry.source_id == "psp"
    assert entry.title_id == "ULJM05800"
    assert entry.display_name == "Monster Hunter Portable 3rd"
    assert "ULJM05800" in entry.path


def test_psp_save_without_sfo(tmp_path: Path):
    save_dir = tmp_path / "PSP" / "SAVEDATA" / "ULES99999"
    save_dir.mkdir(parents=True)
    (save_dir / "DATA.BIN").write_bytes(b"dummy_save_data")

    result = scan(tmp_path)
    assert result.platform == "psp"
    assert len(result.saves) == 1
    entry = result.saves[0]
    assert entry.platform == "psp"
    assert entry.source_id == "psp"
    assert entry.title_id == "ULES99999"
    assert entry.display_name == "ULES99999"


def test_vita_savedata_with_sfo(tmp_path: Path, vita_sfo_bytes: bytes):
    save_dir = tmp_path / "user" / "00" / "savedata" / "PCSE00120"
    sys_dir = save_dir / "sce_sys"
    sys_dir.mkdir(parents=True)
    (sys_dir / "param.sfo").write_bytes(vita_sfo_bytes)
    (save_dir / "savedata.bin").write_bytes(b"vita_save")

    result = scan(tmp_path)
    assert result.platform == "vita"
    assert any(s.source_id == "vita" for s in result.sources)
    assert len(result.saves) == 1
    entry = result.saves[0]
    assert entry.platform == "vita"
    assert entry.source_id == "vita"
    assert entry.title_id == "PCSE00120"
    assert entry.display_name == "Persona 4 Golden"


def test_vita_savedata_uses_matching_app_metadata(
    tmp_path: Path, vita_sfo_bytes: bytes
):
    """A save with only a Title ID inherits its installed app title and icon."""
    save_dir = tmp_path / "user" / "00" / "savedata" / "PCSE00120"
    save_sys = save_dir / "sce_sys"
    save_sys.mkdir(parents=True)
    (save_sys / "param.sfo").write_bytes(
        build_sfo({"TITLE_ID": "PCSE00120", "CATEGORY": "gd"})
    )
    (save_dir / "savedata.bin").write_bytes(b"vita_save")

    app_sys = tmp_path / "app" / "PCSE00120" / "sce_sys"
    app_sys.mkdir(parents=True)
    (app_sys / "param.sfo").write_bytes(vita_sfo_bytes)
    icon = app_sys / "icon0.png"
    icon.write_bytes(b"installed_app_icon")

    result = scan(tmp_path)

    assert len(result.saves) == 1
    entry = result.saves[0]
    assert entry.title_id == "PCSE00120"
    assert entry.display_name == "Persona 4 Golden"
    assert entry.cover_path == str(icon)


def test_vita_savedata_uses_matching_appmeta_metadata(
    tmp_path: Path, vita_sfo_bytes: bytes
):
    """The appmeta sibling is also a valid title/icon fallback."""
    save_dir = tmp_path / "ux0" / "user" / "00" / "savedata" / "PCSG00100"
    save_dir.mkdir(parents=True)
    (save_dir / "savedata.bin").write_bytes(b"vita_save")

    appmeta = tmp_path / "ux0" / "appmeta" / "PCSG00100"
    appmeta.mkdir(parents=True)
    (appmeta / "param.sfo").write_bytes(vita_sfo_bytes)
    icon = appmeta / "icon0.png"
    icon.write_bytes(b"appmeta_icon")

    result = scan(tmp_path)

    assert len(result.saves) == 1
    entry = result.saves[0]
    assert entry.title_id == "PCSG00100"
    assert entry.display_name == "Persona 4 Golden"
    assert entry.cover_path == str(icon)


def test_vita_adrenaline_multi_source(tmp_path: Path, psp_sfo_bytes: bytes, vita_sfo_bytes: bytes):
    # Vita native save under ux0/user/00/savedata
    vita_save = tmp_path / "ux0" / "user" / "00" / "savedata" / "PCSE00120" / "sce_sys"
    vita_save.mkdir(parents=True)
    (vita_save / "param.sfo").write_bytes(vita_sfo_bytes)

    # PSP Adrenaline save under pspemu/PSP/SAVEDATA
    psp_save = tmp_path / "pspemu" / "PSP" / "SAVEDATA" / "ULJM05800"
    psp_save.mkdir(parents=True)
    (psp_save / "PARAM.SFO").write_bytes(psp_sfo_bytes)

    result = scan(tmp_path)
    source_ids = {s.source_id for s in result.sources}
    assert "vita" in source_ids
    assert "psp" in source_ids
    assert len(result.saves) == 2
    platforms = {s.platform for s in result.saves}
    assert platforms == {"vita", "psp"}


def test_vita_exported_savegames(tmp_path: Path):
    save_dir = tmp_path / "data" / "savegames" / "PCSG00100"
    save_dir.mkdir(parents=True)
    (save_dir / "slot0").mkdir()
    (save_dir / "slot0" / "data.bin").write_bytes(b"export_data")

    result = scan(tmp_path)
    assert any(s.source_id == "vita_exported" for s in result.sources)
    assert len(result.saves) == 1
    entry = result.saves[0]
    assert entry.platform == "vita"
    assert entry.source_id == "vita_exported"
    assert entry.title_id == "PCSG00100"


def test_switch_checkpoint_slots(tmp_path: Path):
    game_dir = tmp_path / "switch" / "Checkpoint" / "saves" / "0100000000010000 Super Mario Odyssey"
    slot1 = game_dir / "2026-01-01_12-00"
    slot2 = game_dir / "2026-02-01_15-30"
    slot1.mkdir(parents=True)
    slot2.mkdir(parents=True)
    (slot1 / "save.bin").write_bytes(b"data1")
    (slot2 / "save.bin").write_bytes(b"data2")

    result = scan(tmp_path)
    assert result.platform == "switch"
    assert any(s.source_id == "switch_checkpoint" for s in result.sources)
    assert len(result.saves) == 2
    slots = {s.slot for s in result.saves}
    assert slots == {"2026-01-01_12-00", "2026-02-01_15-30"}
    assert all(s.title_id == "0100000000010000" for s in result.saves)
    assert all(s.display_name == "Super Mario Odyssey" for s in result.saves)


def test_switch_checkpoint_without_slots(tmp_path: Path):
    game_dir = tmp_path / "switch" / "Checkpoint" / "saves" / "Mario Kart 8"
    game_dir.mkdir(parents=True)
    (game_dir / "save.bin").write_bytes(b"data")

    result = scan(tmp_path)
    assert len(result.saves) == 1
    assert result.saves[0].display_name == "Mario Kart 8"
    assert result.saves[0].slot is None


def test_switch_jksv_nested_and_shallow_layouts(tmp_path: Path):
    # Nested with user + slot
    backup_nested = tmp_path / "JKSV" / "The Legend of Zelda BotW" / "Link" / "Slot 1"
    backup_nested.mkdir(parents=True)
    (backup_nested / "game_data.sav").write_bytes(b"botw_save")

    # Shallow layout: game / slot
    backup_shallow = tmp_path / "JKSV" / "Animal Crossing" / "Island Backup"
    backup_shallow.mkdir(parents=True)
    (backup_shallow / "main.dat").write_bytes(b"ac_save")

    # Direct game directory with no subdirs
    backup_direct = tmp_path / "JKSV" / "Smash Ultimate"
    backup_direct.mkdir(parents=True)
    (backup_direct / "save.dat").write_bytes(b"smash_save")

    result = scan(tmp_path)
    assert result.platform == "switch"
    assert any(s.source_id == "switch_jksv" for s in result.sources)
    assert len(result.saves) == 3


def test_switch_sd_only_atmosphere_or_switch_dir(tmp_path: Path):
    (tmp_path / "switch").mkdir()
    result = scan(tmp_path)
    assert result.platform == "switch"
    assert len(result.sources) == 1
    assert result.sources[0].source_id == "switch_sd"
    assert len(result.saves) == 0


def test_3ds_checkpoint_slots(tmp_path: Path):
    game_dir = tmp_path / "3ds" / "Checkpoint" / "saves" / "0x011C4 Pokemon Moon"
    slot_dir = game_dir / "MainSave"
    slot_dir.mkdir(parents=True)
    (slot_dir / "main").write_bytes(b"pkmn_moon")

    result = scan(tmp_path)
    assert result.platform == "3ds"
    assert any(s.source_id == "3ds_checkpoint" for s in result.sources)
    assert len(result.saves) == 1
    entry = result.saves[0]
    assert entry.platform == "3ds"
    assert entry.source_id == "3ds_checkpoint"
    assert entry.title_id == "0x011C4"
    assert entry.display_name == "Pokemon Moon"
    assert entry.slot == "MainSave"


def test_3ds_checkpoint_without_slots(tmp_path: Path):
    game_dir = tmp_path / "3ds" / "Checkpoint" / "saves" / "DirectGame"
    game_dir.mkdir(parents=True)
    (game_dir / "save.bin").write_bytes(b"direct_save")

    result = scan(tmp_path)
    assert len(result.saves) == 1
    assert result.saves[0].display_name == "DirectGame"
    assert result.saves[0].slot is None


def test_3ds_sd_encrypted_container(tmp_path: Path):
    enc_dir = tmp_path / "Nintendo 3DS" / "abcdef0123456789"
    enc_dir.mkdir(parents=True)
    (enc_dir / "00000001.sav").write_bytes(b"encrypted_raw_bytes")

    result = scan(tmp_path)
    assert result.platform == "3ds"
    assert len(result.sources) == 1
    source = result.sources[0]
    assert source.source_id == "3ds_sd"
    assert source.extra and source.extra.get("encrypted_container") is True
    # Invariant: Nintendo 3DS/ encrypted files MUST NOT be treated as manageable save entries
    assert len(result.saves) == 0


def test_unknown_directory(tmp_path: Path):
    (tmp_path / "Movies").mkdir()
    (tmp_path / "Documents").mkdir()
    (tmp_path / "test.txt").write_text("hello")

    result = scan(tmp_path)
    assert result.platform == "unknown"
    assert len(result.sources) == 0
    assert len(result.saves) == 0


def test_corrupt_sfo_does_not_crash(tmp_path: Path):
    save_dir = tmp_path / "PSP" / "SAVEDATA" / "ULUS00001"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(b"CORRUPTED_NON_PSF_HEADER_DATA")

    result = scan(tmp_path)
    assert len(result.saves) == 1
    entry = result.saves[0]
    assert entry.platform == "psp"
    assert entry.display_name == "ULUS00001"


def test_symlink_outside_root_skipped(tmp_path: Path):
    outside_dir = tmp_path / "outside_secret"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("forbidden")

    vault_root = tmp_path / "mock_usb"
    vault_root.mkdir()
    psp_savedata = vault_root / "PSP" / "SAVEDATA"
    psp_savedata.mkdir(parents=True)

    # Symlink pointing to outside
    escaped_link = psp_savedata / "ESCAPED_DIR"
    try:
        escaped_link.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks not supported on this platform/filesystem")

    result = scan(vault_root)
    # Symlink outside vault_root must be skipped
    assert not any(s.title_id == "ESCAPED_DIR" for s in result.saves)


def test_readonly_integrity(tmp_path: Path, psp_sfo_bytes: bytes):
    save_dir = tmp_path / "PSP" / "SAVEDATA" / "ULUS11111"
    save_dir.mkdir(parents=True)
    sfo_file = save_dir / "PARAM.SFO"
    sfo_file.write_bytes(psp_sfo_bytes)

    mtime_before = sfo_file.stat().st_mtime_ns
    scan(tmp_path)
    mtime_after = sfo_file.stat().st_mtime_ns

    assert mtime_before == mtime_after


def test_nonexistent_or_unreadable_root(tmp_path: Path):
    missing_root = tmp_path / "does_not_exist"
    result = scan(missing_root)
    assert result.platform == "unknown"
    assert len(result.warnings) > 0


def test_directory_access_error(monkeypatch, tmp_path: Path):
    psp_dir = tmp_path / "PSP" / "SAVEDATA"
    psp_dir.mkdir(parents=True)

    def broken_iterdir(path, warnings):
        warnings.append("Simulated permission denied")
        return []

    monkeypatch.setattr("vajsave.platforms.common.safe_iterdir", broken_iterdir)
    monkeypatch.setattr("vajsave.scanner._safe_iterdir", broken_iterdir)
    # Platform modules bind safe_iterdir at import time — patch those too.
    for mod_name in (
        "vajsave.platforms.psp",
        "vajsave.platforms.vita",
        "vajsave.platforms.switch",
        "vajsave.platforms.threeds",
        "vajsave.platforms.gba",
        "vajsave.platforms.nds",
    ):
        monkeypatch.setattr(f"{mod_name}.safe_iterdir", broken_iterdir)
    result = scan(tmp_path)
    assert any("Simulated permission denied" in w for w in result.warnings)


def test_scan_cache_reuses_directory_and_path_queries(monkeypatch, tmp_path: Path):
    from vajsave.platforms import common

    calls = {"iterdir": 0, "resolve": 0}
    original_iterdir = Path.iterdir
    original_resolve = Path.resolve

    def counted_iterdir(path):
        calls["iterdir"] += 1
        return original_iterdir(path)

    def counted_resolve(path, *args, **kwargs):
        calls["resolve"] += 1
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "iterdir", counted_iterdir)
    monkeypatch.setattr(Path, "resolve", counted_resolve)
    root_resolved = original_resolve(tmp_path)
    warnings = []

    with common.scan_cache():
        assert common.safe_iterdir(tmp_path, warnings) == common.safe_iterdir(
            tmp_path, warnings
        )
        assert common.resolved_key(tmp_path) == common.resolved_key(tmp_path)
        assert common.is_safe_path(tmp_path, root_resolved)
        assert common.is_safe_path(tmp_path, root_resolved)

    assert calls["iterdir"] == 1
    assert calls["resolve"] == 1


def test_scan_psp_savedata_directory_directly(tmp_path: Path, psp_sfo_bytes: bytes):
    """User selected PSP/SAVEDATA (not card root) should still yield PSP saves."""
    savedata = tmp_path / "PSP" / "SAVEDATA"
    save_dir = savedata / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    (save_dir / "MHP3RD.DAT").write_bytes(b"dummy_save_data")

    result = scan(savedata)
    assert result.platform == "psp"
    assert any(s.source_id == "psp" for s in result.sources)
    assert len(result.saves) == 1
    assert result.saves[0].title_id == "ULJM05800"
    assert result.saves[0].display_name == "Monster Hunter Portable 3rd"


def test_scan_psp_single_game_directory(tmp_path: Path, psp_sfo_bytes: bytes):
    """User selected one game folder under SAVEDATA should yield that PSP save."""
    save_dir = tmp_path / "PSP" / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    (save_dir / "MHP3RD.DAT").write_bytes(b"dummy_save_data")

    result = scan(save_dir)
    assert result.platform == "psp"
    assert any(s.source_id == "psp" for s in result.sources)
    assert len(result.saves) == 1
    entry = result.saves[0]
    assert entry.title_id == "ULJM05800"
    assert entry.display_name == "Monster Hunter Portable 3rd"
    assert Path(entry.path).resolve() == save_dir.resolve()


def test_scan_jksv_wrapped_one_level(tmp_path: Path):
    backup = tmp_path / "SD_Backup_2024"
    slot = backup / "JKSV" / "Zelda BOTW" / "Link" / "Slot1"
    slot.mkdir(parents=True)
    (slot / "save.dat").write_bytes(b"botw")

    result = scan(tmp_path)
    assert result.platform == "switch"
    assert any(s.source_id == "switch_jksv" for s in result.sources)
    assert len(result.saves) >= 1
    assert any(s.display_name == "Zelda BOTW" for s in result.saves)


def test_scan_jksv_wrapped_two_levels(tmp_path: Path):
    slot = tmp_path / "outer" / "inner" / "JKSV" / "Animal Crossing" / "Island"
    slot.mkdir(parents=True)
    (slot / "main.dat").write_bytes(b"ac")

    result = scan(tmp_path)
    assert result.platform == "switch"
    assert any(s.source_id == "switch_jksv" for s in result.sources)
    assert len(result.saves) >= 1
    assert any(s.display_name == "Animal Crossing" for s in result.saves)


def test_scan_switch_checkpoint_wrapped_one_and_two_levels(tmp_path: Path):
    slot1 = (
        tmp_path / "wrap1" / "switch" / "Checkpoint" / "saves"
        / "0100000000010000 Super Mario Odyssey" / "2026-01-01"
    )
    slot1.mkdir(parents=True)
    (slot1 / "save.bin").write_bytes(b"d1")

    slot2 = (
        tmp_path / "a" / "b" / "switch" / "Checkpoint" / "saves"
        / "Mario Kart 8" / "slotA"
    )
    slot2.mkdir(parents=True)
    (slot2 / "save.bin").write_bytes(b"d2")

    result = scan(tmp_path)
    assert result.platform == "switch"
    assert any(s.source_id == "switch_checkpoint" for s in result.sources)
    names = {s.display_name for s in result.saves}
    assert "Super Mario Odyssey" in names
    assert "Mario Kart 8" in names


def test_scan_jksv_directory_directly(tmp_path: Path):
    """User selected the JKSV folder itself."""
    jksv = tmp_path / "JKSV"
    slot = jksv / "Smash Ultimate" / "Backup1"
    slot.mkdir(parents=True)
    (slot / "save.dat").write_bytes(b"smash")

    result = scan(jksv)
    assert result.platform == "switch"
    assert any(s.source_id == "switch_jksv" for s in result.sources)
    assert len(result.saves) == 1
    assert result.saves[0].display_name == "Smash Ultimate"


def test_scan_does_not_duplicate_card_root_psp(tmp_path: Path, psp_sfo_bytes: bytes):
    """Card-root scan must keep prior behavior: one PSP source/save, no dupes from descent."""
    save_dir = tmp_path / "PSP" / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)

    result = scan(tmp_path)
    assert result.platform == "psp"
    assert len(result.sources) == 1
    assert result.sources[0].source_id == "psp"
    assert len(result.saves) == 1


def test_scan_psp_folder_parent_of_savedata(tmp_path: Path, psp_sfo_bytes: bytes):
    """Selecting the PSP directory (parent of SAVEDATA) should still find saves."""
    psp = tmp_path / "PSP"
    save_dir = psp / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)

    result = scan(psp)
    assert result.platform == "psp"
    assert len(result.saves) == 1
    assert result.saves[0].title_id == "ULJM05800"


def test_scan_psp_renamed_savedata_container(tmp_path: Path, psp_sfo_bytes: bytes):
    """A non-SAVEDATA-named folder that holds PARAM.SFO game dirs is still recognized."""
    container = tmp_path / "MyPSPBackup"
    save_dir = container / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)

    result = scan(container)
    assert result.platform == "psp"
    assert len(result.saves) == 1
    assert result.saves[0].display_name == "Monster Hunter Portable 3rd"


def test_scan_switch_checkpoint_dir_directly(tmp_path: Path):
    saves = tmp_path / "switch" / "Checkpoint" / "saves"
    slot = saves / "0100000000010000 Super Mario Odyssey" / "slot1"
    slot.mkdir(parents=True)
    (slot / "save.bin").write_bytes(b"x")

    result = scan(saves)
    assert result.platform == "switch"
    assert any(s.source_id == "switch_checkpoint" for s in result.sources)
    assert len(result.saves) == 1
    assert result.saves[0].display_name == "Super Mario Odyssey"

    result_cp = scan(tmp_path / "switch" / "Checkpoint")
    assert result_cp.platform == "switch"
    assert len(result_cp.saves) == 1


def test_scan_3ds_checkpoint_dir_directly(tmp_path: Path):
    saves = tmp_path / "3ds" / "Checkpoint" / "saves"
    slot = saves / "0x011C4 Pokemon Moon" / "Main"
    slot.mkdir(parents=True)
    (slot / "main").write_bytes(b"pkmn")

    result = scan(saves)
    assert result.platform == "3ds"
    assert any(s.source_id == "3ds_checkpoint" for s in result.sources)
    assert len(result.saves) == 1
    assert result.saves[0].display_name == "Pokemon Moon"

    result_cp = scan(tmp_path / "3ds" / "Checkpoint")
    assert result_cp.platform == "3ds"
    assert len(result_cp.saves) == 1


def test_scan_3ds_checkpoint_wrapped(tmp_path: Path):
    slot = (
        tmp_path / "bak" / "3ds" / "Checkpoint" / "saves"
        / "0x011C4 Pokemon Moon" / "Main"
    )
    slot.mkdir(parents=True)
    (slot / "main").write_bytes(b"pkmn")

    result = scan(tmp_path)
    assert result.platform == "3ds"
    assert any(s.source_id == "3ds_checkpoint" for s in result.sources)
    assert len(result.saves) == 1


def test_scan_empty_jksv_is_not_a_source(tmp_path: Path):
    jksv = tmp_path / "JKSV"
    jksv.mkdir()
    (jksv / "readme.txt").write_text("empty")

    result = scan(tmp_path)
    assert not any(s.source_id == "switch_jksv" for s in result.sources)
    assert len(result.saves) == 0


def test_find_pattern_dirs_wrapper_depth_one_unit():
    """Exercise max_wrapper_depth=1 branch of the pattern finder."""
    from pathlib import Path
    import tempfile
    from vajsave.scanner import _find_pattern_dirs

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        target = root / "wrap" / "JKSV"
        target.mkdir(parents=True)
        deeper = root / "a" / "b" / "JKSV"
        deeper.mkdir(parents=True)
        warnings: list = []
        found = _find_pattern_dirs(root, ("JKSV",), root.resolve(), warnings, max_wrapper_depth=1)
        names = {p.resolve() for p in found}
        assert target.resolve() in names
        assert deeper.resolve() not in names


def test_gba_ezflash_saver(tmp_path: Path):
    saver = tmp_path / "SAVER"
    saver.mkdir()
    sav = saver / "Pokemon Emerald.sav"
    sav.write_bytes(b"gba_ez")

    result = scan(tmp_path)
    assert result.platform == "gba"
    assert any(s.source_id == "gba_ezflash" for s in result.sources)
    assert len(result.saves) == 1
    entry = result.saves[0]
    assert entry.platform == "gba"
    assert entry.source_id == "gba_ezflash"
    assert entry.display_name == "Pokemon Emerald"
    assert Path(entry.path).resolve() == sav.resolve()


def test_gba_everdrive_gbasys_save(tmp_path: Path):
    save_dir = tmp_path / "GBASYS" / "SAVE"
    save_dir.mkdir(parents=True)
    sav = save_dir / "Metroid.srm"
    sav.write_bytes(b"gba_ed")
    (save_dir / "readme.txt").write_text("ignore")

    result = scan(tmp_path)
    assert result.platform == "gba"
    assert any(s.source_id == "gba_everdrive" for s in result.sources)
    assert len(result.saves) == 1
    entry = result.saves[0]
    assert entry.platform == "gba"
    assert entry.source_id == "gba_everdrive"
    assert entry.display_name == "Metroid"
    assert Path(entry.path).name == "Metroid.srm"


def test_gba_everdrive_pro_edgba(tmp_path: Path):
    game = tmp_path / "EDGBA" / "gamedata" / "Fire Emblem"
    game.mkdir(parents=True)
    bram = game / "bram.sav"
    bram.write_bytes(b"pro_bram")

    result = scan(tmp_path)
    assert result.platform == "gba"
    assert any(s.source_id == "gba_everdrive_pro" for s in result.sources)
    assert len(result.saves) == 1
    entry = result.saves[0]
    assert entry.platform == "gba"
    assert entry.source_id == "gba_everdrive_pro"
    assert entry.display_name == "Fire Emblem"
    assert Path(entry.path).resolve() == bram.resolve()


def test_gba_layouts_wrapped(tmp_path: Path):
    sav = tmp_path / "backup" / "SAVER" / "Zelda.sav"
    sav.parent.mkdir(parents=True)
    sav.write_bytes(b"wrapped")

    result = scan(tmp_path)
    assert any(s.source_id == "gba_ezflash" for s in result.sources)
    assert any(s.display_name == "Zelda" for s in result.saves)


def test_nds_twilight_saves(tmp_path: Path):
    rom_dir = tmp_path / "roms" / "nds"
    rom_dir.mkdir(parents=True)
    (rom_dir / "Mario Kart DS.nds").write_bytes(b"rom")
    saves = rom_dir / "saves"
    saves.mkdir()
    sav = saves / "Mario Kart DS.sav"
    sav.write_bytes(b"nds_tw")

    result = scan(tmp_path)
    assert result.platform == "nds"
    assert any(s.source_id == "nds_twilight" for s in result.sources)
    assert len(result.saves) == 1
    entry = result.saves[0]
    assert entry.platform == "nds"
    assert entry.source_id == "nds_twilight"
    assert entry.display_name == "Mario Kart DS"
    assert Path(entry.path).resolve() == sav.resolve()


def test_nds_sibling_sav_with_card_fingerprint(tmp_path: Path):
    (tmp_path / "_nds").mkdir()
    rom_dir = tmp_path / "games"
    rom_dir.mkdir()
    (rom_dir / "Pokemon Platinum.nds").write_bytes(b"rom")
    sav = rom_dir / "Pokemon Platinum.sav"
    sav.write_bytes(b"sibling")

    result = scan(tmp_path)
    assert result.platform == "nds"
    assert any(s.source_id == "nds_r4" for s in result.sources) or any(
        s.platform == "nds" for s in result.saves
    )
    assert len(result.saves) == 1
    entry = result.saves[0]
    assert entry.platform == "nds"
    assert entry.display_name == "Pokemon Platinum"
    assert Path(entry.path).resolve() == sav.resolve()


def test_nds_stray_sav_without_fingerprint_ignored(tmp_path: Path):
    (tmp_path / "notes.sav").write_bytes(b"not_a_nds_save")
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "memo.sav").write_bytes(b"also_stray")

    result = scan(tmp_path)
    assert result.platform == "unknown"
    assert len(result.saves) == 0
    assert not any(s.platform == "nds" for s in result.sources)


def test_nds_ids_sibling_sav_with_card_fingerprint(tmp_path: Path):
    """A Wood R4 card keeps some NDS dumps as ``.ids`` next to their ``.sav``."""
    (tmp_path / "__rpg").mkdir()
    series = tmp_path / "game" / "马里奥系列"
    series.mkdir(parents=True)
    (series / "摸摸耀西云中漫步.ids").write_bytes(b"rom")
    sav = series / "摸摸耀西云中漫步.sav"
    sav.write_bytes(b"sibling")

    result = scan(tmp_path)
    assert result.platform == "nds"
    assert len(result.saves) == 1
    entry = result.saves[0]
    assert entry.display_name == "摸摸耀西云中漫步"
    assert entry.source_id == "nds_r4"
    assert Path(entry.path).resolve() == sav.resolve()


def test_3ds_jksm_saves_not_switch(tmp_path: Path):
    slot = tmp_path / "JKSV" / "Saves" / "Pokemon Ultra Sun" / "Main"
    slot.mkdir(parents=True)
    (slot / "00000001.sav").write_bytes(b"jksm")

    result = scan(tmp_path)
    assert result.platform == "3ds"
    assert any(s.source_id == "3ds_jksm" for s in result.sources)
    assert not any(s.source_id == "switch_jksv" for s in result.sources)
    assert len(result.saves) == 1
    entry = result.saves[0]
    assert entry.platform == "3ds"
    assert entry.source_id == "3ds_jksm"
    assert entry.display_name == "Pokemon Ultra Sun"
    assert entry.slot == "Main"


def test_switch_jksv_still_works_alongside_reserved_names(tmp_path: Path):
    # Classic Switch game folder
    game_slot = tmp_path / "JKSV" / "Zelda BOTW" / "Link" / "Slot1"
    game_slot.mkdir(parents=True)
    (game_slot / "save.dat").write_bytes(b"botw")

    # Reserved 3DS-style folder must not become a Switch "game"
    reserved = tmp_path / "JKSV" / "Saves" / "Some3DSGame" / "slot0"
    reserved.mkdir(parents=True)
    (reserved / "data.bin").write_bytes(b"3ds")

    result = scan(tmp_path)
    switch_saves = [s for s in result.saves if s.platform == "switch"]
    threeds_saves = [s for s in result.saves if s.platform == "3ds"]
    assert any(s.source_id == "switch_jksv" for s in result.sources)
    assert any(s.source_id == "3ds_jksm" for s in result.sources)
    assert any(s.display_name == "Zelda BOTW" for s in switch_saves)
    assert not any(s.display_name == "Saves" for s in switch_saves)
    assert any(s.display_name == "Some3DSGame" for s in threeds_saves)


def test_guess_platform_psp(tmp_path: Path):
    (tmp_path / "PSP" / "SAVEDATA").mkdir(parents=True)
    assert guess_platform(tmp_path) == "psp"


def test_guess_platform_psp_savedata_selected(tmp_path: Path):
    savedata = tmp_path / "SAVEDATA"
    savedata.mkdir()
    assert guess_platform(savedata) == "psp"


def test_guess_platform_vita_native(tmp_path: Path):
    (tmp_path / "user" / "00" / "savedata").mkdir(parents=True)
    assert guess_platform(tmp_path) == "vita"


def test_guess_platform_vita_exported(tmp_path: Path):
    (tmp_path / "data" / "savegames").mkdir(parents=True)
    assert guess_platform(tmp_path) == "vita"


def test_guess_platform_switch_checkpoint(tmp_path: Path):
    (tmp_path / "switch" / "Checkpoint" / "saves").mkdir(parents=True)
    assert guess_platform(tmp_path) == "switch"


def test_guess_platform_switch_jksv(tmp_path: Path):
    (tmp_path / "JKSV" / "Zelda BOTW" / "Slot1").mkdir(parents=True)
    assert guess_platform(tmp_path) == "switch"


def test_guess_platform_switch_atmosphere(tmp_path: Path):
    (tmp_path / "atmosphere").mkdir()
    assert guess_platform(tmp_path) == "switch"


def test_guess_platform_3ds_jksm(tmp_path: Path):
    (tmp_path / "JKSV" / "Saves" / "Pokemon Moon" / "Main").mkdir(parents=True)
    assert guess_platform(tmp_path) == "3ds"


def test_guess_platform_3ds_checkpoint(tmp_path: Path):
    (tmp_path / "3ds" / "Checkpoint" / "saves").mkdir(parents=True)
    assert guess_platform(tmp_path) == "3ds"


def test_guess_platform_3ds_nintendo_dir(tmp_path: Path):
    (tmp_path / "Nintendo 3DS").mkdir()
    assert guess_platform(tmp_path) == "3ds"


def test_guess_platform_gba_saver(tmp_path: Path):
    (tmp_path / "SAVER").mkdir()
    assert guess_platform(tmp_path) == "gba"


def test_guess_platform_gba_everdrive(tmp_path: Path):
    (tmp_path / "GBASYS" / "SAVE").mkdir(parents=True)
    assert guess_platform(tmp_path) == "gba"


def test_guess_platform_gba_superfw(tmp_path: Path):
    (tmp_path / ".superfw").mkdir()
    assert guess_platform(tmp_path) == "gba"


def test_guess_platform_nds_roms_dir(tmp_path: Path):
    (tmp_path / "roms" / "nds").mkdir(parents=True)
    assert guess_platform(tmp_path) == "nds"


def test_guess_platform_nds_card_fingerprint(tmp_path: Path):
    (tmp_path / "_nds").mkdir()
    assert guess_platform(tmp_path) == "nds"


def test_guess_platform_nds_wood_rpg_fingerprint(tmp_path: Path):
    (tmp_path / "__rpg").mkdir()
    assert guess_platform(tmp_path) == "nds"


def test_guess_platform_unknown_returns_none(tmp_path: Path):
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Movies").mkdir()
    assert guess_platform(tmp_path) is None


def test_guess_platform_nonexistent_returns_none(tmp_path: Path):
    assert guess_platform(tmp_path / "does_not_exist") is None


def test_guess_platform_is_shallow_only(tmp_path: Path):
    # A PSP layout buried deeper than the shallow fingerprint must not be found.
    (tmp_path / "a" / "b" / "PSP" / "SAVEDATA").mkdir(parents=True)
    assert guess_platform(tmp_path) is None


# --- embedded cover discovery (filled during scan) --------------------------


def test_scan_fills_psp_embedded_cover(tmp_path: Path, psp_sfo_bytes: bytes):
    save_dir = tmp_path / "PSP" / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    icon = save_dir / "ICON0.PNG"
    icon.write_bytes(b"not-a-real-png-but-exists")

    result = scan(tmp_path)
    entry = result.saves[0]
    assert entry.cover_path == str(icon)
    assert entry.to_dict()["cover_path"] == str(icon)


def test_scan_fills_vita_sce_sys_cover(tmp_path: Path, vita_sfo_bytes: bytes):
    sce_sys = tmp_path / "user" / "00" / "savedata" / "PCSE00120" / "sce_sys"
    sce_sys.mkdir(parents=True)
    (sce_sys / "param.sfo").write_bytes(vita_sfo_bytes)
    icon = sce_sys / "icon0.png"
    icon.write_bytes(b"icon")

    result = scan(tmp_path)
    entry = result.saves[0]
    assert entry.cover_path == str(icon)


def test_scan_leaves_cover_unset_without_icon(tmp_path: Path, psp_sfo_bytes: bytes):
    save_dir = tmp_path / "PSP" / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)

    result = scan(tmp_path)
    entry = result.saves[0]
    assert entry.cover_path is None
    assert "cover_path" not in entry.to_dict()

import os
import stat
from pathlib import Path
import pytest
from vajsave.scanner import scan
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

    monkeypatch.setattr("vajsave.scanner._safe_iterdir", broken_iterdir)
    result = scan(tmp_path)
    assert any("Simulated permission denied" in w for w in result.warnings)

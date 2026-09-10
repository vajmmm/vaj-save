from datetime import datetime
from pathlib import Path

from vajsave.app_state import AppState
from vajsave.library import (
    backup_save,
    copy_save_tree,
    destination_for,
    hash_tree,
    import_save,
    load_catalog,
    restore_snapshot,
    sanitize_name,
)
from vajsave.models import SaveEntry, VolumeInfo
from vajsave.volume import FakeVolumeProvider
from conftest import build_sfo


def test_sanitize_name_strips_unsafe_chars():
    assert sanitize_name('PSP/<>:"save') == "PSP_____save"
    assert sanitize_name("   ") == "untitled"


def test_copy_save_tree_does_not_modify_source(tmp_path: Path):
    src = tmp_path / "ULJM05800"
    src.mkdir()
    data = src / "DATA.BIN"
    data.write_bytes(b"save-bytes")
    before = data.stat().st_mtime_ns
    dest = tmp_path / "library-out"
    copied = copy_save_tree(src, dest)
    assert (copied / "DATA.BIN").read_bytes() == b"save-bytes"
    assert data.read_bytes() == b"save-bytes"
    assert data.stat().st_mtime_ns == before


def test_copy_skips_symlinks(tmp_path: Path):
    src = tmp_path / "save"
    src.mkdir()
    (src / "real.bin").write_bytes(b"ok")
    outside = tmp_path / "secret.txt"
    outside.write_text("nope")
    try:
        (src / "link").symlink_to(outside)
    except OSError:
        return
    copied = copy_save_tree(src, tmp_path / "out")
    assert (copied / "real.bin").is_file()
    assert not (copied / "link").exists()


def test_import_save_uses_platform_title_slot(tmp_path: Path):
    src = tmp_path / "PCSE00120"
    src.mkdir()
    (src / "data.bin").write_bytes(b"vita")
    entry = SaveEntry(
        platform="vita",
        source_id="vita",
        display_name="Persona 4 Golden",
        path=str(src),
        title_id="PCSE00120",
        slot="SLOT0",
    )
    when = datetime(2026, 9, 9, 12, 30, 0)
    dest = import_save(entry, tmp_path / "lib", when)
    expected = destination_for(entry, tmp_path / "lib", when)
    assert dest == expected
    assert (dest / "PCSE00120" / "data.bin").read_bytes() == b"vita"


def test_app_state_import_selected_and_visible(tmp_path: Path, psp_sfo_bytes: bytes):
    root = tmp_path / "PSP_VOL"
    save_dir = root / "PSP" / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    (save_dir / "DATA.BIN").write_bytes(b"DATA")
    library = tmp_path / "Documents" / "vaj-save"

    state = AppState(
        provider=FakeVolumeProvider([VolumeInfo(name="PSP", mount_point=root)]),
        library_root=library,
    )
    state.select_mount(root)
    entry = state.visible_saves()[0]
    dest = state.import_save(entry)
    assert dest is not None
    assert dest.exists()
    assert "已保存到本地" in state.status_text
    assert (library / "psp" / "ULJM05800").exists()
    assert (save_dir / "DATA.BIN").read_bytes() == b"DATA"

    copied = state.import_visible_saves()
    assert len(copied) == 1
    assert copied[0].exists()


def test_import_missing_path_sets_warning(tmp_path: Path):
    state = AppState(library_root=tmp_path / "lib")
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="gone",
        path=str(tmp_path / "missing"),
        title_id="ULUS00000",
    )
    assert state.import_save(entry) is None
    assert state.warnings


def test_backup_dedupes_identical_content(tmp_path: Path):
    src = tmp_path / "ULUS11111"
    src.mkdir()
    (src / "DATA.BIN").write_bytes(b"same")
    entry = SaveEntry(platform="psp", source_id="psp", display_name="Demo", path=str(src), title_id="ULUS11111")
    lib = tmp_path / "lib"
    first = backup_save(entry, lib, datetime(2026, 1, 1, 10, 0, 0))
    second = backup_save(entry, lib, datetime(2026, 1, 2, 10, 0, 0))
    assert first.is_new is True
    assert second.is_new is False
    assert first.snapshot.sha256 == second.snapshot.sha256
    catalog = load_catalog(lib)
    assert len(catalog.games[first.game.id].versions) == 1


def test_backup_creates_new_version_when_changed(tmp_path: Path):
    src = tmp_path / "ULUS11111"
    src.mkdir()
    payload = src / "DATA.BIN"
    payload.write_bytes(b"v1")
    entry = SaveEntry(platform="psp", source_id="psp", display_name="Demo", path=str(src), title_id="ULUS11111")
    lib = tmp_path / "lib"
    first = backup_save(entry, lib, datetime(2026, 1, 1, 10, 0, 0))
    payload.write_bytes(b"v2")
    second = backup_save(entry, lib, datetime(2026, 1, 2, 10, 0, 0))
    assert second.is_new is True
    assert first.snapshot.sha256 != second.snapshot.sha256
    catalog = load_catalog(lib)
    assert len(catalog.games[first.game.id].versions) == 2


def test_restore_snapshot_copies_to_destination(tmp_path: Path):
    src = tmp_path / "ULUS11111"
    src.mkdir()
    (src / "DATA.BIN").write_bytes(b"keep-me")
    entry = SaveEntry(platform="psp", source_id="psp", display_name="Demo", path=str(src), title_id="ULUS11111")
    lib = tmp_path / "lib"
    result = backup_save(entry, lib, datetime(2026, 1, 1, 10, 0, 0))
    dest = tmp_path / "restore-out"
    restored = restore_snapshot(result.snapshot, lib, dest)
    assert restored.exists()
    files = list(restored.rglob("DATA.BIN"))
    assert files and files[0].read_bytes() == b"keep-me"
    assert hash_tree(src) == result.snapshot.sha256

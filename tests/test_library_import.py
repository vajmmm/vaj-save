import json
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from vajsave.app_state import AppState
from vajsave.library import backup_save, game_key, hash_tree, load_catalog
from vajsave.library_import import (
    FORMAT,
    FORMAT_VERSION,
    MANIFEST_NAME,
    export_snapshot_zip,
    import_snapshot_zip,
)
from vajsave.models import SaveEntry


def _write_save(root: Path, name: str, payload: bytes) -> Path:
    src = root / name
    src.mkdir()
    (src / "DATA.BIN").write_bytes(payload)
    return src


def _psp_entry(path: Path, title_id: str, display_name: str) -> SaveEntry:
    return SaveEntry(
        platform="psp",
        source_id="psp",
        display_name=display_name,
        path=str(path),
        title_id=title_id,
    )


def test_export_contains_manifest_and_payload(tmp_path: Path):
    src = _write_save(tmp_path, "ULJM05800", b"persona")
    entry = _psp_entry(src, "ULJM05800", "Persona 2")
    lib = tmp_path / "lib"
    result = backup_save(
        entry, lib, datetime(2026, 1, 1, 12, 0, 0), identity_key="psp:ULJM05800"
    )

    zipped = export_snapshot_zip(
        result.snapshot, lib, tmp_path / "persona.zip", game=result.game
    )

    assert zipped.is_file()
    with zipfile.ZipFile(zipped) as zf:
        names = zf.namelist()
        assert MANIFEST_NAME in names
        payload = [
            name
            for name in names
            if name.replace("\\", "/").startswith("payload/")
            and not name.endswith("/")
        ]
        assert payload
        assert any(name.replace("\\", "/").endswith("DATA.BIN") for name in payload)
        manifest = json.loads(zf.read(MANIFEST_NAME))

    assert manifest["format"] == FORMAT
    assert manifest["format_version"] == FORMAT_VERSION
    assert manifest["platform"] == "psp"
    assert manifest["title_id"] == "ULJM05800"
    assert manifest["display_name"] == "Persona 2"
    assert manifest["identity_key"] == "psp:ULJM05800"
    assert manifest["slot"] == "default"
    assert manifest["user"] is None
    assert manifest["created_at"] == result.snapshot.created_at
    assert manifest["sha256"] == result.snapshot.sha256


def test_import_round_trip_attaches_by_identity_key(tmp_path: Path):
    src = _write_save(tmp_path, "ULJM05800", b"persona")
    export_lib = tmp_path / "export-lib"
    exported = backup_save(
        _psp_entry(src, "ULJM05800", "Persona 2"),
        export_lib,
        datetime(2026, 1, 1, 12, 0, 0),
        identity_key="psp:ULJM05800",
    )
    zip_path = export_snapshot_zip(
        exported.snapshot, export_lib, tmp_path / "p2.zip", game=exported.game
    )

    lib = tmp_path / "lib"
    placeholder = _write_save(tmp_path, "OTHER0001", b"placeholder")
    existing = backup_save(
        _psp_entry(placeholder, "OTHER0001", "Placeholder"),
        lib,
        datetime(2026, 1, 1, 8, 0, 0),
        identity_key="psp:ULJM05800",
    )

    imported = import_snapshot_zip(lib, zip_path)
    assert imported.game.id == existing.game.id
    assert imported.game.identity_key == "psp:ULJM05800"
    assert imported.snapshot.sha256 == exported.snapshot.sha256
    catalog = load_catalog(lib)
    assert list(catalog.games) == [existing.game.id]
    assert len(catalog.games[existing.game.id].versions) == 2


def test_import_identical_content_is_not_new(tmp_path: Path):
    src = _write_save(tmp_path, "ULUS11111", b"same-bytes")
    entry = _psp_entry(src, "ULUS11111", "Demo")
    lib = tmp_path / "lib"
    result = backup_save(entry, lib, datetime(2026, 1, 1, 10, 0, 0))
    zip_path = export_snapshot_zip(result.snapshot, lib, tmp_path / "demo.zip")

    imported = import_snapshot_zip(lib, zip_path)
    assert imported.is_new is False
    assert imported.snapshot.sha256 == result.snapshot.sha256
    assert imported.snapshot.id == result.snapshot.id
    catalog = load_catalog(lib)
    assert len(catalog.games[result.game.id].versions) == 1


def test_zip_slip_is_rejected(tmp_path: Path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "catalog.json").write_text(
        json.dumps({"version": 1, "games": []}), encoding="utf-8"
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    zip_path = tmp_path / "evil.zip"
    manifest = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "platform": "psp",
        "title_id": "EVIL0001",
        "display_name": "Evil",
        "identity_key": None,
        "slot": "default",
        "user": None,
        "created_at": "2026-01-01T00:00:00",
        "sha256": "abc",
    }
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest))
        zf.writestr("../outside.txt", "pwned")

    with pytest.raises(ValueError, match="不安全|zip slip|路径"):
        import_snapshot_zip(lib, zip_path)

    assert outside.read_text(encoding="utf-8") == "secret"
    assert load_catalog(lib).games == {}
    assert {path.name for path in lib.iterdir()} == {"catalog.json"}


def test_legacy_zip_without_manifest_requires_attach_or_new_fields(tmp_path: Path):
    save = tmp_path / "LEGACY01"
    save.mkdir()
    (save / "DATA.BIN").write_bytes(b"legacy-bytes")
    zip_path = tmp_path / "legacy.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(save / "DATA.BIN", arcname="DATA.BIN")

    lib = tmp_path / "lib"
    with pytest.raises(ValueError):
        import_snapshot_zip(lib, zip_path)
    assert not (lib / "catalog.json").exists() or load_catalog(lib).games == {}

    created = import_snapshot_zip(
        lib,
        zip_path,
        new_platform="psp",
        new_display_name="Legacy Game",
        new_title_id="LEGACY01",
    )
    assert created.is_new is True
    assert created.game.platform == "psp"
    assert created.game.display_name == "Legacy Game"
    assert created.game.title_id == "LEGACY01"
    assert created.game.id == game_key(
        _psp_entry(save, "LEGACY01", "Legacy Game")
    )
    assert created.snapshot.sha256 == hash_tree(save / "DATA.BIN")

    attached = import_snapshot_zip(lib, zip_path, attach_game_id=created.game.id)
    assert attached.is_new is False
    assert attached.game.id == created.game.id
    catalog = load_catalog(lib)
    assert len(catalog.games) == 1
    assert len(catalog.games[created.game.id].versions) == 1


def test_app_state_import_snapshot_zip(tmp_path: Path):
    src = _write_save(tmp_path, "ULUS11111", b"via-app-state")
    src_lib = tmp_path / "src-lib"
    result = backup_save(
        _psp_entry(src, "ULUS11111", "Demo"),
        src_lib,
        datetime(2026, 1, 2, 10, 0, 0),
    )
    zip_path = export_snapshot_zip(result.snapshot, src_lib, tmp_path / "app")
    state = AppState(library_root=tmp_path / "lib")
    imported = state.import_snapshot_zip(zip_path)
    assert imported.is_new is True
    assert imported.game.platform == "psp"
    assert imported.game.title_id == "ULUS11111"
    assert imported.snapshot.sha256 == result.snapshot.sha256

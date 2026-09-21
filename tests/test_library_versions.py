"""Deleting a single snapshot from the local library.

The versions layer owns per-snapshot removal: drop one safe payload, rewrite
the catalog, and if that was the last remaining version, call delete_game so
covers, notes, and stars disappear with the catalog entry. Device saves are
never touched.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image

from vajsave.app_state import AppState
from vajsave.artwork.cache import CoverCache
from vajsave.covers import DOWNLOADED_COVER_DIR
from vajsave.library import (
    Catalog,
    GameRecord,
    Snapshot,
    backup_save,
    game_key,
    load_catalog,
    save_catalog,
    set_game_meta,
)
from vajsave.library_versions import delete_snapshot
from vajsave.models import SaveEntry
from vajsave.volume import FakeVolumeProvider


def _png(color=(30, 120, 220, 255)) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (4, 4), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _backup(
    tmp_path: Path,
    lib: Path,
    platform: str,
    title_id: str,
    name: str,
    when=None,
    *,
    identity_key=None,
    payload: bytes = None,
):
    folder = tmp_path / "src" / platform / title_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "save.bin").write_bytes(payload or (title_id + name).encode("utf-8"))
    entry = SaveEntry(
        platform=platform,
        source_id=platform,
        display_name=name,
        path=str(folder),
        title_id=title_id,
    )
    backup_save(entry, lib, when=when, identity_key=identity_key)
    return entry


def test_delete_middle_snapshot_keeps_game(tmp_path: Path):
    lib = tmp_path / "lib"
    entry = _backup(
        tmp_path, lib, "psp", "ULJM05800", "PSP Game", datetime(2024, 1, 1), payload=b"v1"
    )
    _backup(tmp_path, lib, "psp", "ULJM05800", "PSP Game", datetime(2024, 2, 1), payload=b"v2")
    other = _backup(tmp_path, lib, "gba", "AGBE01", "GBA Game", datetime(2024, 3, 1), payload=b"other")

    game_id = game_key(entry)
    catalog = load_catalog(lib)
    game = catalog.games[game_id]
    game.starred = True
    game.note = "keep this"
    save_catalog(lib, catalog)
    first, second = game.versions
    first_dir = lib / first.path
    second_dir = lib / second.path
    assert first_dir.exists() and second_dir.exists()

    result = delete_snapshot(lib, game_id, first.id)

    assert result.ok is True
    assert result.found is True
    assert result.removed is True
    assert result.game_removed is False
    assert not result.errors
    assert not first_dir.exists()
    assert second_dir.exists()
    catalog = load_catalog(lib)
    remaining = catalog.games[game_id].versions
    assert [snap.id for snap in remaining] == [second.id]
    assert catalog.games[game_id].starred is True
    assert catalog.games[game_id].note == "keep this"
    other_id = game_key(other)
    assert other_id in catalog.games
    assert (lib / catalog.games[other_id].versions[0].path).exists()


def test_delete_last_snapshot_removes_game(tmp_path: Path):
    lib = tmp_path / "lib"
    key = "gba:sha1:" + "a" * 40
    entry = _backup(
        tmp_path,
        lib,
        "gba",
        "AGBE01",
        "GBA Game",
        datetime(2024, 1, 1),
        identity_key=key,
        payload=b"only",
    )
    game_id = game_key(entry)
    set_game_meta(lib, entry, starred=True, note="will vanish")
    cache = CoverCache(lib / DOWNLOADED_COVER_DIR)
    cover = cache.store("gba", key, _png(), canonical_title="GBA Game")
    snap = load_catalog(lib).games[game_id].versions[0]
    snap_dir = lib / snap.path
    assert snap_dir.exists()
    assert cover.exists()

    result = delete_snapshot(lib, game_id, snap.id)

    assert result.ok is True
    assert result.game_removed is True
    assert result.found is True
    assert result.removed is True
    assert not snap_dir.exists()
    assert game_id not in load_catalog(lib).games
    assert not cover.exists()
    assert key not in CoverCache(lib / DOWNLOADED_COVER_DIR).manifest()


def test_delete_snapshot_does_not_touch_device_files(tmp_path: Path):
    lib = tmp_path / "lib"
    folder = tmp_path / "device" / "ULJM05800"
    folder.mkdir(parents=True)
    (folder / "save.bin").write_bytes(b"device save")
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="PSP Game",
        path=str(folder),
        title_id="ULJM05800",
    )
    backup_save(entry, lib)
    snap = load_catalog(lib).games[game_key(entry)].versions[0]

    result = delete_snapshot(lib, game_key(entry), snap.id)

    assert result.ok is True
    assert folder.exists()
    assert (folder / "save.bin").read_bytes() == b"device save"


def test_delete_missing_snapshot_found_false(tmp_path: Path):
    lib = tmp_path / "lib"
    entry = _backup(tmp_path, lib, "psp", "ULJM05800", "PSP Game")
    game_id = game_key(entry)
    before = load_catalog(lib).games[game_id].versions[:]

    result = delete_snapshot(lib, game_id, "does-not-exist")

    assert result.found is False
    assert result.removed is False
    assert result.game_removed is False
    assert result.ok is False
    catalog = load_catalog(lib)
    assert game_id in catalog.games
    assert [snap.id for snap in catalog.games[game_id].versions] == [snap.id for snap in before]

    missing_game = delete_snapshot(lib, "psp:missing:default", "v1")
    assert missing_game.found is False
    assert missing_game.removed is False
    assert missing_game.ok is False


def test_delete_unsafe_snapshot_keeps_catalog(tmp_path: Path):
    lib = tmp_path / "lib"
    lib.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.bin"
    marker.write_bytes(b"do not delete")

    game_id = "psp:GAME:default"
    game = GameRecord(
        id=game_id,
        platform="psp",
        title_id="GAME",
        display_name="Game",
        versions=[
            Snapshot(
                id="v1",
                created_at="2024-01-01T00:00:00",
                sha256="x",
                source_path=str(marker),
                path="../../outside",
            )
        ],
    )
    save_catalog(lib, Catalog({game_id: game}))

    result = delete_snapshot(lib, game_id, "v1")

    assert result.ok is False
    assert result.found is True
    assert result.removed is False
    assert result.game_removed is False
    assert result.errors
    assert marker.exists()
    assert game_id in load_catalog(lib).games


def test_appstate_delete_library_snapshot(tmp_path: Path):
    lib = tmp_path / "lib"
    _backup(tmp_path, lib, "psp", "ULJM05800", "PSP Game", datetime(2024, 1, 1), payload=b"v1")
    _backup(tmp_path, lib, "psp", "ULJM05800", "PSP Game", datetime(2024, 2, 1), payload=b"v2")

    state = AppState(provider=FakeVolumeProvider([]), library_root=lib)
    state.set_library_mode(True)
    entry = state.visible_saves()[0]
    versions = state.versions_for_entry(entry)
    first = versions[0]

    result = state.delete_library_snapshot(entry, first)

    assert result.ok is True
    assert result.game_removed is False
    remaining = state.versions_for_entry(entry)
    assert [snap.id for snap in remaining] == [versions[1].id]

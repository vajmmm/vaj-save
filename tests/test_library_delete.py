"""Deleting a game from the local library and its cached covers.

The library layer owns the whole operation: remove every safe snapshot payload,
update the catalog, and drop the covers that belong to that single game (the
identity-hash downloaded cover plus any user cover keyed by stem).  It must
never touch a device save, the covers/ directory itself, the library root, or
another game's artwork.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image

from vajsave.artwork.cache import CoverCache
from vajsave.covers import DOWNLOADED_COVER_DIR
from vajsave.library import (
    backup_save,
    delete_game,
    game_key,
    load_catalog,
)
from vajsave.models import SaveEntry


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


def test_delete_game_removes_all_snapshots_and_catalog_entry(tmp_path: Path):
    lib = tmp_path / "lib"
    entry = _backup(tmp_path, lib, "psp", "ULJM05800", "PSP Game", datetime(2024, 1, 1), payload=b"v1")
    _backup(tmp_path, lib, "psp", "ULJM05800", "PSP Game", datetime(2024, 2, 1), payload=b"v2")
    other = _backup(tmp_path, lib, "gba", "AGBE01", "GBA Game", datetime(2024, 3, 1), payload=b"other")

    psp_id = game_key(entry)
    snapshot_dirs = [Path(lib) / snap.path for snap in load_catalog(lib).games[psp_id].versions]
    assert len(snapshot_dirs) == 2

    result = delete_game(lib, psp_id)

    assert result.ok is True
    assert result.removed is True
    assert result.snapshots_removed == 2
    assert result.snapshots_retained == 0
    for directory in snapshot_dirs:
        assert not directory.exists()
    catalog = load_catalog(lib)
    assert psp_id not in catalog.games
    # The other game is untouched.
    other_id = game_key(other)
    assert other_id in catalog.games
    assert (lib / catalog.games[other_id].versions[0].path).exists()


def test_delete_game_removes_downloaded_cover_and_manifest_entry(tmp_path: Path):
    lib = tmp_path / "lib"
    key = "gba:sha1:" + "a" * 40
    other_key = "gba:sha1:" + "b" * 40
    entry = _backup(tmp_path, lib, "gba", "AGBE01", "GBA Game", identity_key=key)

    cache = CoverCache(lib / DOWNLOADED_COVER_DIR)
    cover = cache.store("gba", key, _png(), canonical_title="GBA Game")
    other_cover = cache.store("gba", other_key, _png(), canonical_title="Other")

    result = delete_game(lib, game_key(entry))

    assert result.ok is True
    assert not cover.exists()
    assert str(cover) in result.covers_removed
    fresh = CoverCache(lib / DOWNLOADED_COVER_DIR)
    assert key not in fresh.manifest()
    # The unrelated cover survives.
    assert other_cover.exists()
    assert other_key in fresh.manifest()


def test_delete_game_removes_user_cover_by_stem(tmp_path: Path):
    lib = tmp_path / "lib"
    entry = _backup(tmp_path, lib, "psp", "ULJM05800", "PSP Game")
    covers = lib / DOWNLOADED_COVER_DIR / "psp"
    covers.mkdir(parents=True, exist_ok=True)
    by_title_id = covers / "ULJM05800.png"
    by_name = covers / "PSP Game.jpg"
    upper_ext = covers / "ULJM05800.PNG"
    unrelated = covers / "SOMETHING_ELSE.png"
    for path in (by_title_id, by_name, upper_ext, unrelated):
        path.write_bytes(_png())

    result = delete_game(lib, game_key(entry))

    assert result.ok is True
    assert not by_title_id.exists()
    assert not by_name.exists()
    assert not upper_ext.exists()
    assert unrelated.exists()


def test_delete_game_without_key_keeps_hash_cover_when_not_unique(tmp_path: Path):
    lib = tmp_path / "lib"
    entry = _backup(tmp_path, lib, "gba", "AGBE01", "GBA Game")  # no identity_key
    cache = CoverCache(lib / DOWNLOADED_COVER_DIR)
    # A manifest entry on the same platform but a different title must never be
    # guessed as this game's cover.
    key = "gba:sha1:" + "c" * 40
    cover = cache.store("gba", key, _png(), canonical_title="A Completely Different Game")

    result = delete_game(lib, game_key(entry))

    assert result.ok is True
    assert cover.exists()
    assert key in CoverCache(lib / DOWNLOADED_COVER_DIR).manifest()


def test_delete_game_without_key_removes_a_unique_title_match(tmp_path: Path):
    lib = tmp_path / "lib"
    entry = _backup(tmp_path, lib, "gba", "AGBE01", "Apotris")  # no identity_key
    cache = CoverCache(lib / DOWNLOADED_COVER_DIR)
    key = "gba:sha1:" + "d" * 40
    cover = cache.store("gba", key, _png(), canonical_title="Apotris")

    result = delete_game(lib, game_key(entry))

    assert result.ok is True
    assert not cover.exists()
    assert key not in CoverCache(lib / DOWNLOADED_COVER_DIR).manifest()


def test_delete_game_never_touches_library_root_or_other_games(tmp_path: Path):
    lib = tmp_path / "lib"
    first = _backup(tmp_path, lib, "psp", "ULJM05800", "PSP Game")
    second = _backup(tmp_path, lib, "psp", "ULJM05801", "Other Game")
    covers = lib / DOWNLOADED_COVER_DIR / "psp"
    covers.mkdir(parents=True, exist_ok=True)
    keep = covers / "ULJM05801.png"
    keep.write_bytes(_png())

    result = delete_game(lib, game_key(first))

    assert result.ok is True
    assert lib.exists() and lib.is_dir()
    assert (lib / DOWNLOADED_COVER_DIR).is_dir()
    assert keep.exists()
    assert game_key(second) in load_catalog(lib).games


def test_delete_game_partial_failure_is_not_ok(tmp_path: Path, monkeypatch):
    import vajsave.library as library_module

    lib = tmp_path / "lib"
    entry = _backup(tmp_path, lib, "psp", "ULJM05800", "PSP Game", payload=b"v1")
    _backup(tmp_path, lib, "psp", "ULJM05800", "PSP Game", payload=b"v2")
    game_id = game_key(entry)
    before = len(load_catalog(lib).games[game_id].versions)

    monkeypatch.setattr(library_module, "_delete_snapshot_payload", lambda snap, root: False)

    result = delete_game(lib, game_id)

    assert result.ok is False
    assert result.removed is False
    assert result.errors
    # The retained snapshots and the catalog entry survive.
    catalog = load_catalog(lib)
    assert game_id in catalog.games
    assert len(catalog.games[game_id].versions) == before


def test_delete_game_missing_is_a_noop(tmp_path: Path):
    lib = tmp_path / "lib"
    lib.mkdir()
    result = delete_game(lib, "psp:missing:default")
    assert result.found is False
    assert result.removed is False
    assert result.ok is False


def test_delete_game_never_touches_the_device_source(tmp_path: Path):
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

    result = delete_game(lib, game_key(entry))

    assert result.ok is True
    assert folder.exists()
    assert (folder / "save.bin").read_bytes() == b"device save"


def test_delete_game_retains_snapshot_escaping_the_library(tmp_path: Path):
    from vajsave.library import Catalog, GameRecord, Snapshot, save_catalog

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

    result = delete_game(lib, game_id)

    assert result.ok is False
    assert result.snapshots_retained == 1
    assert marker.exists()
    assert game_id in load_catalog(lib).games

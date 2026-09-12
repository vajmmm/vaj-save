import json
from datetime import datetime
from pathlib import Path

from vajsave.app_state import AppState
from vajsave.library import (
    Catalog,
    GameRecord,
    Snapshot,
    backup_save,
    classify_save_status,
    copy_save_tree,
    destination_for,
    game_key,
    hash_tree,
    import_save,
    load_catalog,
    path_mtime_iso,
    restore_snapshot,
    sanitize_name,
)
from vajsave.models import SaveEntry, VolumeInfo
from vajsave.volume import FakeVolumeProvider
from conftest import build_sfo


def test_classify_new_does_not_hash(monkeypatch):
    entry = SaveEntry(platform="psp", source_id="psp", display_name="X", path="/tmp/x", title_id="ULUS1")

    def boom(_path):
        raise AssertionError("new saves must not be hashed")

    monkeypatch.setattr("vajsave.library.hash_tree", boom)
    status = classify_save_status(entry, Catalog())
    assert status.status == "new"
    assert status.sha256 is None


def test_hash_tree_file_and_cache(tmp_path: Path):
    import hashlib

    blob = b"abc" * 1000
    f = tmp_path / "one.bin"
    f.write_bytes(blob)
    expected = hashlib.sha256(b"file\0" + blob).hexdigest()
    first = hash_tree(f)
    second = hash_tree(f)
    assert first == expected
    assert second == expected

    nested = tmp_path / "dir"
    nested.mkdir()
    (nested / "a.bin").write_bytes(b"aaaa")
    (nested / "b.bin").write_bytes(b"bbbb")
    once = hash_tree(nested)
    twice = hash_tree(nested)
    assert once == twice
    (nested / "a.bin").write_bytes(b"cccc")
    assert hash_tree(nested) != once


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
    # Backed-up identical content is unchanged; default lists every save.
    assert state.save_status(entry).status == "unchanged"
    assert len(state.visible_saves()) == 1

    # backup-visible with "仅显示有更新" skips hidden unchanged
    state.toggle_hide_unchanged()
    assert state.visible_saves() == []
    copied = state.import_visible_saves()
    assert copied == []

    state.toggle_hide_unchanged()
    assert len(state.visible_saves()) == 1
    # Re-backup visible (shown unchanged) still succeeds via dedupe path.
    copied_again = state.import_visible_saves()
    assert len(copied_again) == 1
    assert copied_again[0].exists()


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


def _write_settings(lib: Path, keep_last) -> None:
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "settings.json").write_text(
        json.dumps({"keep_last": keep_last}),
        encoding="utf-8",
    )


def _make_entry(tmp_path: Path, title_id: str = "ULUS22222") -> tuple[Path, SaveEntry]:
    src = tmp_path / title_id
    src.mkdir(exist_ok=True)
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="PruneDemo",
        path=str(src),
        title_id=title_id,
    )
    return src, entry


def test_identical_hash_backup_reuses_and_does_not_prune(tmp_path: Path):
    src, entry = _make_entry(tmp_path)
    payload = src / "DATA.BIN"
    lib = tmp_path / "lib"
    _write_settings(lib, 1)

    payload.write_bytes(b"v1")
    first = backup_save(entry, lib, datetime(2026, 1, 1, 10, 0, 0))
    payload.write_bytes(b"v2")
    second = backup_save(entry, lib, datetime(2026, 1, 2, 10, 0, 0))
    # identical re-backup of newest content must reuse and must not prune the only kept version
    third = backup_save(entry, lib, datetime(2026, 1, 3, 10, 0, 0))

    assert third.is_new is False
    assert third.snapshot.sha256 == second.snapshot.sha256
    catalog = load_catalog(lib)
    versions = catalog.games[first.game.id].versions
    assert len(versions) == 1
    assert versions[0].sha256 == second.snapshot.sha256
    assert second.path.exists()


def test_keep_last_one_retains_only_newest(tmp_path: Path):
    src, entry = _make_entry(tmp_path)
    payload = src / "DATA.BIN"
    lib = tmp_path / "lib"
    _write_settings(lib, 1)

    paths = []
    for i, day in enumerate((1, 2, 3), start=1):
        payload.write_bytes(f"v{i}".encode())
        result = backup_save(entry, lib, datetime(2026, 1, day, 10, 0, 0))
        paths.append(result.path)

    catalog = load_catalog(lib)
    versions = catalog.games[game_key(entry)].versions
    assert len(versions) == 1
    assert versions[0].id == "2026-01-03_10-00-00"
    assert paths[-1].exists()
    assert not paths[0].exists()
    assert not paths[1].exists()


def test_keep_last_zero_never_prunes(tmp_path: Path):
    src, entry = _make_entry(tmp_path)
    payload = src / "DATA.BIN"
    lib = tmp_path / "lib"
    _write_settings(lib, 0)

    for i, day in enumerate((1, 2, 3), start=1):
        payload.write_bytes(f"v{i}".encode())
        backup_save(entry, lib, datetime(2026, 1, day, 10, 0, 0))

    catalog = load_catalog(lib)
    versions = catalog.games[game_key(entry)].versions
    assert len(versions) == 3
    for snap in versions:
        assert snap.absolute_path(lib).exists()


def test_missing_settings_falls_back_to_keep_last_ten(tmp_path: Path):
    src, entry = _make_entry(tmp_path)
    payload = src / "DATA.BIN"
    lib = tmp_path / "lib"
    # no settings.json

    results = []
    for i in range(1, 12):
        payload.write_bytes(f"v{i}".encode())
        results.append(
            backup_save(entry, lib, datetime(2026, 1, min(i, 28), 10, 0, i % 60))
        )

    catalog = load_catalog(lib)
    versions = catalog.games[game_key(entry)].versions
    assert len(versions) == 10
    assert not results[0].path.exists()
    assert results[-1].path.exists()
    assert all(r.path.exists() for r in results[-10:])


def test_invalid_settings_falls_back_to_keep_last_ten_without_crash(tmp_path: Path):
    src, entry = _make_entry(tmp_path)
    payload = src / "DATA.BIN"
    lib = tmp_path / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "settings.json").write_text("{not-json", encoding="utf-8")

    payload.write_bytes(b"ok")
    result = backup_save(entry, lib, datetime(2026, 1, 1, 10, 0, 0))
    assert result.is_new is True
    assert result.path.exists()

    # also non-dict / bad keep_last values
    for bad in ("[]", '{"keep_last": "x"}', '{"keep_last": -3}', '{"keep_last": true}', "null"):
        (lib / "settings.json").write_text(bad, encoding="utf-8")
        payload.write_bytes(bad.encode())
        backup_save(entry, lib, datetime(2026, 2, 1, 10, 0, 0))


def test_prune_never_deletes_paths_outside_library_root(tmp_path: Path):
    from vajsave.library import (
        Catalog,
        GameRecord,
        Snapshot,
        game_key,
        prune_game_versions,
        save_catalog,
    )

    lib = tmp_path / "lib"
    lib.mkdir()
    outside = tmp_path / "outside_secret"
    outside.mkdir()
    marker = outside / "do-not-delete.bin"
    marker.write_bytes(b"safe")

    # craft a snapshot whose relative path escapes library_root via ..
    escape_rel = Path("..") / "outside_secret"
    snap_escape = Snapshot(
        id="evil",
        created_at="2026-01-01T00:00:00",
        sha256="a",
        source_path="/tmp",
        path=escape_rel.as_posix(),
    )
    snap_keep = Snapshot(
        id="keep",
        created_at="2026-01-02T00:00:00",
        sha256="b",
        source_path="/tmp",
        path="psp/safe/slot/2026-01-02_00-00-00",
    )
    (lib / snap_keep.path).mkdir(parents=True)
    (lib / snap_keep.path / "x.bin").write_bytes(b"in-lib")

    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="x",
        path=str(tmp_path / "src"),
        title_id="SAFE0001",
    )
    game = GameRecord(
        id=game_key(entry),
        platform="psp",
        title_id="SAFE0001",
        display_name="x",
        versions=[snap_escape, snap_keep],
    )
    catalog = Catalog({game.id: game})
    save_catalog(lib, catalog)

    prune_game_versions(game, lib, keep_last=1)
    save_catalog(lib, Catalog({game.id: game}))

    assert marker.exists()
    assert marker.read_bytes() == b"safe"
    # unsafe/escaped path must not be deleted and must stay in catalog
    assert [s.id for s in game.versions] == ["evil", "keep"]
    assert (lib / snap_keep.path).exists()


def test_catalog_write_remains_atomic_after_prune(tmp_path: Path):
    src, entry = _make_entry(tmp_path)
    payload = src / "DATA.BIN"
    lib = tmp_path / "lib"
    _write_settings(lib, 1)

    payload.write_bytes(b"a")
    backup_save(entry, lib, datetime(2026, 1, 1, 10, 0, 0))
    payload.write_bytes(b"b")
    backup_save(entry, lib, datetime(2026, 1, 2, 10, 0, 0))

    catalog_file = lib / "catalog.json"
    assert catalog_file.is_file()
    assert not catalog_file.with_suffix(".json.tmp").exists()
    data = json.loads(catalog_file.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert len(data["games"][0]["versions"]) == 1


def test_load_keep_last_edge_cases(tmp_path: Path):
    from vajsave.library import DEFAULT_KEEP_LAST, load_keep_last

    lib = tmp_path / "lib"
    lib.mkdir()
    assert load_keep_last(lib) == DEFAULT_KEEP_LAST

    (lib / "settings.json").write_text("{}", encoding="utf-8")
    assert load_keep_last(lib) == DEFAULT_KEEP_LAST

    (lib / "settings.json").write_bytes(b"\xff\xfe not-utf8")
    assert load_keep_last(lib) == DEFAULT_KEEP_LAST

    (lib / "settings.json").write_text('{"keep_last": 3}', encoding="utf-8")
    assert load_keep_last(lib) == 3


def test_prune_skips_library_root_and_deletes_file_payload(tmp_path: Path):
    from vajsave.library import GameRecord, Snapshot, prune_game_versions

    lib = tmp_path / "lib"
    lib.mkdir()
    file_snap_path = Path("psp") / "file.bin"
    file_abs = lib / file_snap_path
    file_abs.parent.mkdir(parents=True)
    file_abs.write_bytes(b"old")

    root_snap = Snapshot(
        id="root",
        created_at="2026-01-01T00:00:00",
        sha256="r",
        source_path="/tmp",
        path=".",
    )
    file_snap = Snapshot(
        id="file",
        created_at="2026-01-02T00:00:00",
        sha256="f",
        source_path="/tmp",
        path=file_snap_path.as_posix(),
    )
    keep_snap = Snapshot(
        id="keep",
        created_at="2026-01-03T00:00:00",
        sha256="k",
        source_path="/tmp",
        path="psp/keep/2026-01-03_00-00-00",
    )
    (lib / keep_snap.path).mkdir(parents=True)

    game = GameRecord(
        id="psp:x:default",
        platform="psp",
        title_id="x",
        display_name="x",
        versions=[root_snap, file_snap, keep_snap],
    )
    prune_game_versions(game, lib, keep_last=1)
    # root (library root itself) is unsafe: retained; file payload deleted+dropped; newest kept
    assert [s.id for s in game.versions] == ["root", "keep"]
    assert lib.exists()
    assert not file_abs.exists()


def test_prune_rmtree_oserror_keeps_catalog_and_directory(tmp_path: Path, monkeypatch):
    """rmtree OSError must keep both catalog entries and the old directory."""
    import vajsave.library as libmod
    from vajsave.library import GameRecord, Snapshot, prune_game_versions

    lib = tmp_path / "lib"
    lib.mkdir()
    snap_path = "psp/t/slot/old"
    old_dir = lib / snap_path
    old_dir.mkdir(parents=True)
    (old_dir / "x.bin").write_bytes(b"x")
    keep_path = "psp/t/slot/new"
    (lib / keep_path).mkdir(parents=True)

    old = Snapshot(
        id="old",
        created_at="2026-01-01T00:00:00",
        sha256="o",
        source_path="/tmp",
        path=snap_path,
    )
    new = Snapshot(
        id="new",
        created_at="2026-01-02T00:00:00",
        sha256="n",
        source_path="/tmp",
        path=keep_path,
    )
    game = GameRecord(
        id="g",
        platform="psp",
        title_id="t",
        display_name="t",
        versions=[old, new],
    )

    def boom(*_a, **_k):
        raise OSError("denied")

    monkeypatch.setattr(libmod.shutil, "rmtree", boom)
    prune_game_versions(game, lib, keep_last=1)
    assert [s.id for s in game.versions] == ["old", "new"]
    assert old_dir.exists()
    assert (old_dir / "x.bin").read_bytes() == b"x"
    assert (lib / keep_path).exists()


def test_backup_save_without_display_name_still_prunes(tmp_path: Path):
    src = tmp_path / "ULUS33333"
    src.mkdir()
    payload = src / "DATA.BIN"
    lib = tmp_path / "lib"
    _write_settings(lib, 1)
    entry = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="",
        path=str(src),
        title_id="ULUS33333",
    )
    payload.write_bytes(b"one")
    first = backup_save(entry, lib, datetime(2026, 3, 1, 10, 0, 0))
    payload.write_bytes(b"two")
    second = backup_save(entry, lib, datetime(2026, 3, 2, 10, 0, 0))
    catalog = load_catalog(lib)
    assert len(catalog.games[game_key(entry)].versions) == 1
    assert not first.path.exists()
    assert second.path.exists()


def test_prune_missing_payload_is_noop(tmp_path: Path):
    from vajsave.library import GameRecord, Snapshot, prune_game_versions

    lib = tmp_path / "lib"
    lib.mkdir()
    missing = Snapshot(
        id="gone",
        created_at="2026-01-01T00:00:00",
        sha256="g",
        source_path="/tmp",
        path="psp/missing/2026-01-01_00-00-00",
    )
    keep = Snapshot(
        id="keep",
        created_at="2026-01-02T00:00:00",
        sha256="k",
        source_path="/tmp",
        path="psp/keep/2026-01-02_00-00-00",
    )
    (lib / keep.path).mkdir(parents=True)
    game = GameRecord(
        id="g",
        platform="psp",
        title_id="t",
        display_name="t",
        versions=[missing, keep],
    )
    prune_game_versions(game, lib, keep_last=1)
    assert [s.id for s in game.versions] == ["keep"]


def test_safe_path_and_delete_resolve_oserror(tmp_path: Path, monkeypatch):
    import vajsave.library as libmod
    from pathlib import Path as PathCls
    from vajsave.library import (
        GameRecord,
        Snapshot,
        _delete_snapshot_payload,
        _is_safe_library_path,
        prune_game_versions,
    )

    lib = tmp_path / "lib"
    lib.mkdir()
    inside = lib / "psp" / "ok.bin"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"z")

    real_resolve = PathCls.resolve

    def flaky_resolve(self, *args, **kwargs):
        text = str(self)
        if text.endswith("ok.bin") or text.endswith("boom"):
            raise OSError("resolve failed")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(PathCls, "resolve", flaky_resolve)
    assert _is_safe_library_path(inside, lib) is False

    snap = Snapshot(
        id="boom",
        created_at="2026-01-01T00:00:00",
        sha256="z",
        source_path="/tmp",
        path="psp/ok.bin",
    )
    # First safety check may fail via resolve OSError; ensure delete is no-op.
    _delete_snapshot_payload(snap, lib)

    # Force path that passes a first check then fails second resolve in delete:
    # use a normal path, patch only the delete-time resolve after safety.
    monkeypatch.setattr(PathCls, "resolve", real_resolve)
    assert _is_safe_library_path(inside, lib) is True

    calls = {"n": 0}
    original = libmod._is_safe_library_path

    def sometimes_safe(target, library_root):
        calls["n"] += 1
        if calls["n"] == 1:
            return True
        return False

    monkeypatch.setattr(libmod, "_is_safe_library_path", sometimes_safe)

    def resolve_once_oserror(self, *args, **kwargs):
        # allow root resolve in absolute_path callers; fail only payload resolve path
        return real_resolve(self, *args, **kwargs)

    # Cover the post-resolve early return branch (line after second safety check).
    _delete_snapshot_payload(snap, lib)
    assert inside.exists()

    monkeypatch.setattr(libmod, "_is_safe_library_path", original)

    # Cover resolve OSError inside _delete_snapshot_payload try block.
    def resolve_raises(self, *args, **kwargs):
        raise OSError("nope")

    # Make first safety pass without resolve by stubbing it True, then fail resolve.
    monkeypatch.setattr(libmod, "_is_safe_library_path", lambda *a, **k: True)
    monkeypatch.setattr(PathCls, "resolve", resolve_raises)
    _delete_snapshot_payload(snap, lib)


def test_path_mtime_iso_missing_and_file(tmp_path: Path):
    missing = tmp_path / "nope"
    assert path_mtime_iso(missing) is None
    f = tmp_path / "save.bin"
    f.write_bytes(b"x")
    assert path_mtime_iso(f) is not None


def test_classify_hash_error_and_internal_hash_fail(tmp_path: Path):
    src = tmp_path / "ULUS99999"
    src.mkdir()
    (src / "DATA.BIN").write_bytes(b"x")
    entry = SaveEntry(platform="psp", source_id="psp", display_name="X", path=str(src), title_id="ULUS99999")
    catalog = Catalog()
    status = classify_save_status(entry, catalog, hash_error=True)
    assert status.status == "new"
    assert status.sha256 is None

    missing = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="gone",
        path=str(tmp_path / "missing-dir"),
        title_id="ULUS00001",
    )
    status2 = classify_save_status(missing, catalog)
    assert status2.status == "new"


def test_classify_changed_without_stale_when_dates_unparseable(tmp_path: Path):
    src = tmp_path / "ULUS88888"
    src.mkdir()
    (src / "DATA.BIN").write_bytes(b"live")
    entry = SaveEntry(platform="psp", source_id="psp", display_name="Y", path=str(src), title_id="ULUS88888")
    key = game_key(entry)
    catalog = Catalog(
        {
            key: GameRecord(
                id=key,
                platform="psp",
                title_id="ULUS88888",
                display_name="Y",
                versions=[
                    Snapshot(
                        id="bad",
                        created_at="not-a-date",
                        sha256="deadbeef",
                        source_path=str(src),
                        path="psp/x",
                    )
                ],
            )
        }
    )
    digest = hash_tree(src)
    status = classify_save_status(entry, catalog, digest=digest)
    assert status.status == "changed"
    assert status.mtime_stale is False


def test_import_selected_empty_sets_status(tmp_path: Path):
    state = AppState(library_root=tmp_path / "lib")
    assert state.import_selected_saves([]) == []
    assert "没有可备份" in state.status_text


# --- app config (application settings) tests ---


def test_config_path_respects_override_and_platform(tmp_path: Path, monkeypatch):
    from vajsave import library as libmod

    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "custom.json"))
    assert libmod.config_path() == tmp_path / "custom.json"

    monkeypatch.delenv("VAJSAVE_CONFIG_PATH", raising=False)
    monkeypatch.setattr(libmod.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert libmod.config_path() == tmp_path / "xdg" / "vaj-save" / "config.json"
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert libmod.config_path() == Path.home() / ".config" / "vaj-save" / "config.json"

    monkeypatch.setattr(libmod.sys, "platform", "darwin")
    assert libmod.config_path() == Path.home() / "Library" / "Application Support" / "vaj-save" / "config.json"

    monkeypatch.setattr(libmod.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    assert libmod.config_path() == tmp_path / "appdata" / "vaj-save" / "config.json"


def test_app_config_roundtrip(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "nested" / "config.json"
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(cfg))
    from vajsave.library import config_path, load_app_config, save_app_config

    assert config_path() == cfg
    assert load_app_config() == {}

    lib_root = tmp_path / "My Library"
    save_app_config({"library_root": str(lib_root)})
    loaded = load_app_config()
    assert loaded["library_root"] == str(lib_root)
    assert cfg.is_file()
    # atomic write must not leave a temp file behind
    assert not cfg.with_suffix(".json.tmp").exists()


def test_app_config_missing_and_bad_json_degrades(tmp_path: Path, monkeypatch):
    from vajsave.library import load_app_config

    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "missing.json"))
    assert load_app_config() == {}

    cfg = tmp_path / "config.json"
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(cfg))
    for bad in ("{not json", "[]", "null", '"text"'):
        cfg.write_text(bad, encoding="utf-8")
        assert load_app_config() == {}
    cfg.write_bytes(b"\xff\xfe\x00bad")
    assert load_app_config() == {}


def test_save_app_config_unsupported_payload_returns_false(tmp_path: Path, monkeypatch):
    from vajsave.library import save_app_config

    cfg = tmp_path / "config.json"
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(cfg))

    # A non-serialisable value must degrade to False instead of raising, and must
    # not leave a half-written temp file next to the config.
    assert save_app_config({"library_root": object()}) is False
    assert not cfg.exists()
    assert not cfg.with_name(cfg.name + ".tmp").exists()


def test_save_app_config_failure_is_safe(tmp_path: Path, monkeypatch):
    from vajsave.library import save_app_config

    # Parent path is a file, so the directory cannot be created.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(blocker / "config.json"))
    assert save_app_config({"library_root": "/tmp/x"}) is False


# --- local library browsing rows --------------------------------------------


def test_catalog_entries_one_row_per_game_newest_first(tmp_path: Path):
    """One row per catalog game, across platforms, most recent backup first."""
    from vajsave.library import catalog_entries

    lib = tmp_path / "lib"
    source = tmp_path / "src"

    def _make(platform: str, title_id: str, name: str, when: datetime) -> None:
        folder = source / platform / title_id
        folder.mkdir(parents=True)
        (folder / "save.bin").write_bytes((title_id + name).encode("utf-8"))
        entry = SaveEntry(
            platform=platform,
            source_id=platform,
            display_name=name,
            path=str(folder),
            title_id=title_id,
        )
        backup_save(entry, lib, when=when)

    _make("psp", "ULJM05800", "Older PSP", datetime(2024, 1, 1, 10, 0, 0))
    _make("gba", "AGBE01", "Newer GBA", datetime(2024, 5, 1, 10, 0, 0))
    _make("nds", "ADME01", "Middle NDS", datetime(2024, 3, 1, 10, 0, 0))

    catalog = load_catalog(lib)
    entries = catalog_entries(catalog, lib)

    assert [e.display_name for e in entries] == ["Newer GBA", "Middle NDS", "Older PSP"]
    assert all(e.source_id == "library" for e in entries)
    assert len({e.extra["library_game_id"] for e in entries}) == 3

    newest = entries[0]
    game = catalog.games[newest.extra["library_game_id"]]
    assert newest.path == str(game.versions[-1].absolute_path(lib))
    # The synthesized entry must recompute back to the exact catalog key so
    # versions/notes/starring keep resolving against the same game.
    assert game_key(newest) == newest.extra["library_game_id"]


def test_catalog_entries_empty_catalog_returns_empty(tmp_path: Path):
    from vajsave.library import catalog_entries

    assert catalog_entries(Catalog(), tmp_path / "lib") == []

"""Persistent ROM identity cache and hot-path binding write tests.

The UI rebuilds every save row on each refresh and calls the identity resolver
for each row.  Without a cache each call re-digests the whole matched ROM and
rewrites the same binding record.  These tests pin down the two optimisations:
the derived ROM identity is remembered across resolver instances, and an
unchanged binding is never rewritten.
"""

from pathlib import Path

import vajsave.identity.roms as roms_module
from vajsave.identity import BINDINGS_NAME, GameIdentityResolver
from vajsave.identity.bindings import BindingStore
from vajsave.models import SaveEntry


def make_gba_rom(title="KIRBY GAME", code="KBRD", size=0x200) -> bytes:
    data = bytearray(size)
    data[0xA0:0xA0 + 12] = title.ljust(12)[:12].encode("ascii")
    data[0xAC:0xB0] = code.ljust(4)[:4].encode("ascii")
    return bytes(data)


def entry(platform: str, name: str, path: Path) -> SaveEntry:
    return SaveEntry(
        platform=platform,
        source_id=f"{platform}_test",
        display_name=name,
        path=str(path),
    )


def _rom_dir(tmp_path: Path) -> Path:
    d = tmp_path / "roms"
    d.mkdir()
    return d


# --- persistent digest cache -------------------------------------------------


def test_rom_identity_cache_avoids_repeated_digest_across_resolvers(tmp_path: Path, monkeypatch):
    rom_dir = _rom_dir(tmp_path)
    (rom_dir / "Kirby.gba").write_bytes(make_gba_rom())
    cache_path = tmp_path / "rom_cache.json"
    sav = entry("gba", "Kirby", tmp_path / "SAVER" / "Kirby.sav")

    calls = []
    real_digest = roms_module.digest_file

    def counting_digest(path, *args, **kwargs):
        calls.append(str(path))
        return real_digest(path, *args, **kwargs)

    monkeypatch.setattr(roms_module, "digest_file", counting_digest)

    first = GameIdentityResolver(
        rom_dirs={"gba": [rom_dir]},
        binding_path=tmp_path / "a.json",
        cache_path=cache_path,
    )
    assert first.resolve(sav).is_resolved
    assert len(calls) == 1
    assert cache_path.is_file()

    calls.clear()
    # A brand new resolver (e.g. after set_rom_dirs reset the cached one) must
    # reuse the persisted digest instead of hashing the ROM again.
    second = GameIdentityResolver(
        rom_dirs={"gba": [rom_dir]},
        binding_path=tmp_path / "b.json",
        cache_path=cache_path,
    )
    result = second.resolve(sav)
    assert result.is_resolved
    assert result.identity_key.startswith("gba:sha1:")
    assert calls == []


def test_rom_cache_is_invalidated_when_rom_changes(tmp_path: Path, monkeypatch):
    rom_dir = _rom_dir(tmp_path)
    rom = rom_dir / "Kirby.gba"
    rom.write_bytes(make_gba_rom(title="KIRBY ONE"))
    cache_path = tmp_path / "rom_cache.json"
    sav = entry("gba", "Kirby", tmp_path / "Kirby.sav")

    first = GameIdentityResolver(rom_dirs={"gba": [rom_dir]}, cache_path=cache_path)
    first_key = first.resolve(sav).identity_key

    # Replace the ROM with different bytes (different size and mtime).
    rom.write_bytes(make_gba_rom(title="KIRBY TWO", size=0x400))

    calls = []
    real_digest = roms_module.digest_file

    def counting_digest(path, *args, **kwargs):
        calls.append(str(path))
        return real_digest(path, *args, **kwargs)

    monkeypatch.setattr(roms_module, "digest_file", counting_digest)
    second = GameIdentityResolver(rom_dirs={"gba": [rom_dir]}, cache_path=cache_path)
    second_result = second.resolve(sav)
    assert second_result.is_resolved
    assert len(calls) == 1
    assert second_result.identity_key != first_key


def test_rom_cache_corrupt_file_degrades_gracefully(tmp_path: Path):
    rom_dir = _rom_dir(tmp_path)
    (rom_dir / "Kirby.gba").write_bytes(make_gba_rom())
    cache_path = tmp_path / "rom_cache.json"
    cache_path.write_text("{not json", encoding="utf-8")

    resolver = GameIdentityResolver(rom_dirs={"gba": [rom_dir]}, cache_path=cache_path)
    result = resolver.resolve(entry("gba", "Kirby", tmp_path / "Kirby.sav"))
    assert result.is_resolved
    # The cache is rewritten into a valid document for the next run.
    assert cache_path.read_text(encoding="utf-8").lstrip().startswith("{")


def test_rom_cache_keeps_header_derived_fields(tmp_path: Path):
    rom_dir = _rom_dir(tmp_path)
    (rom_dir / "Kirby.gba").write_bytes(make_gba_rom(title="KIRBY GAME", code="KBRD"))
    cache_path = tmp_path / "rom_cache.json"
    sav = entry("gba", "Kirby", tmp_path / "Kirby.sav")

    first = GameIdentityResolver(rom_dirs={"gba": [rom_dir]}, cache_path=cache_path)
    before = first.resolve(sav)
    second = GameIdentityResolver(rom_dirs={"gba": [rom_dir]}, cache_path=cache_path)
    after = second.resolve(sav)
    assert after.identity.title == before.identity.title == "KIRBY GAME"
    assert after.identity.game_code == "KBRD"
    assert after.identity.rom_sha1 == before.identity.rom_sha1


# --- binding write de-duplication -------------------------------------------


def test_repeated_resolve_does_not_rewrite_binding(tmp_path: Path, monkeypatch):
    rom_dir = _rom_dir(tmp_path)
    (rom_dir / "Kirby.gba").write_bytes(make_gba_rom())
    binding_path = tmp_path / BINDINGS_NAME
    resolver = GameIdentityResolver(rom_dirs={"gba": [rom_dir]}, binding_path=binding_path)
    sav = entry("gba", "Kirby", tmp_path / "SAVER" / "Kirby.sav")

    saves = []
    real_save = BindingStore.save

    def counting_save(self):
        saves.append(1)
        return real_save(self)

    monkeypatch.setattr(BindingStore, "save", counting_save)

    assert resolver.resolve(sav).is_resolved
    assert len(saves) == 1
    # A second resolve sees the identical record and must not touch the disk.
    assert resolver.resolve(sav).is_resolved
    assert len(saves) == 1


def test_binding_store_set_skips_identical_record(tmp_path: Path):
    store = BindingStore(tmp_path / "bindings.json")
    from vajsave.identity import GameIdentity

    entry_obj = entry("gba", "Kirby", tmp_path / "Kirby.sav")
    identity = GameIdentity(identity_key="gba:sha1:aa", platform="gba", title="Kirby", rom_sha1="aa")

    store.set(entry_obj, identity, manual=False)
    before = store.all()
    store.set(entry_obj, identity, manual=False)
    assert store.all() == before


# --- cache failure paths -----------------------------------------------------


def test_binding_store_save_failure_is_reported(tmp_path: Path):
    from vajsave.identity import GameIdentity

    blocker = tmp_path / "blocker"
    blocker.write_text("file, not a directory", encoding="utf-8")
    store = BindingStore(blocker / "bindings.json")
    # A set whose write cannot succeed returns the stored value but reports the
    # failed save instead of raising an unbound-local error on the cleanup path.
    stored = store.set(
        entry("gba", "Kirby", tmp_path / "Kirby.sav"),
        GameIdentity(identity_key="gba:sha1:aa", platform="gba", title="Kirby"),
    )
    assert stored.identity_key == "gba:sha1:aa"
    assert store.save() is False


def test_build_identity_from_rom_without_cache(tmp_path: Path):
    from vajsave.identity import build_identity_from_rom, make_rom_file

    rom = tmp_path / "Kirby.gba"
    rom.write_bytes(make_gba_rom())
    identity = build_identity_from_rom(make_rom_file(rom, "gba"), "gba")
    assert identity is not None
    assert identity.identity_key.startswith("gba:sha1:")


def test_build_identity_from_rom_unreadable_returns_none(tmp_path: Path, monkeypatch):
    from vajsave.identity import build_identity_from_rom, make_rom_file

    rom = tmp_path / "Kirby.gba"
    rom.write_bytes(make_gba_rom())

    def boom(path, *args, **kwargs):
        raise OSError("unreadable")

    monkeypatch.setattr(roms_module, "digest_file", boom)
    assert build_identity_from_rom(make_rom_file(rom, "gba"), "gba") is None


def test_rom_cache_put_skips_unchanged_record(tmp_path: Path, monkeypatch):
    from vajsave.identity import GameIdentity
    from vajsave.identity.cache import RomIdentityCache

    rom = tmp_path / "Kirby.gba"
    rom.write_bytes(make_gba_rom())
    cache = RomIdentityCache(tmp_path / "cache.json")
    identity = GameIdentity(identity_key="gba:sha1:aa", platform="gba", title="K")
    cache.put(rom, "gba", identity)

    writes = []
    monkeypatch.setattr(cache, "save", lambda: writes.append(1))
    cache.put(rom, "gba", identity)
    assert writes == []
    # A missing file cannot be fingerprinted and is silently ignored.
    cache.put(tmp_path / "nope.gba", "gba", identity)
    assert writes == []


def test_rom_cache_load_without_path_stays_empty():
    from vajsave.identity.cache import RomIdentityCache

    cache = RomIdentityCache()
    cache.load()
    assert cache.all() == {}


def test_rom_cache_save_cleans_temp_on_replace_failure(tmp_path: Path):
    from vajsave.identity.cache import RomIdentityCache

    directory = tmp_path / "cache-dir"
    directory.mkdir()
    cache = RomIdentityCache(directory)
    assert cache.save() is False
    assert not (tmp_path / "cache-dir.tmp").exists()


def test_app_state_hot_path_uses_persistent_cache(tmp_path: Path, monkeypatch):
    """Repeated UI-style resolution must not re-digest or rewrite bindings."""
    from vajsave.app_state import AppState
    from vajsave.identity import ROM_CACHE_NAME

    rom_dir = _rom_dir(tmp_path)
    (rom_dir / "Kirby.gba").write_bytes(make_gba_rom())
    lib = tmp_path / "lib"
    state = AppState(library_root=lib)
    state.set_rom_dirs(rom_dir, None)

    calls = []
    real_digest = roms_module.digest_file

    def counting_digest(path, *args, **kwargs):
        calls.append(str(path))
        return real_digest(path, *args, **kwargs)

    monkeypatch.setattr(roms_module, "digest_file", counting_digest)
    save = entry("gba", "Kirby", tmp_path / "SAVER" / "Kirby.sav")

    assert state.resolve_save_identity(save).is_resolved
    assert state.resolve_save_identity(save).is_resolved
    # Reset the resolver (as set_rom_dirs does); the persisted cache still hits.
    state.set_rom_dirs(rom_dir, None)
    assert state.resolve_save_identity(save).is_resolved
    assert len(calls) == 1
    assert (lib / ROM_CACHE_NAME).is_file()


def test_rom_cache_in_memory_roundtrip_without_path(tmp_path: Path):
    from vajsave.identity import GameIdentity
    from vajsave.identity.cache import RomIdentityCache

    rom = tmp_path / "Kirby.gba"
    rom.write_bytes(make_gba_rom())
    cache = RomIdentityCache()
    identity = GameIdentity(identity_key="gba:sha1:aa", platform="gba", title="Kirby")
    cache.put(rom, "gba", identity)
    assert cache.get(rom, "gba").identity_key == "gba:sha1:aa"
    assert cache.save() is False


def test_rom_cache_miss_on_platform_mismatch_and_stale_stats(tmp_path: Path, monkeypatch):
    from vajsave.identity import GameIdentity
    from vajsave.identity.cache import RomIdentityCache

    rom = tmp_path / "Kirby.gba"
    rom.write_bytes(make_gba_rom())
    cache = RomIdentityCache(tmp_path / "cache.json")
    cache.put(rom, "gba", GameIdentity(identity_key="gba:sha1:aa", platform="gba", title="K"))

    assert cache.get(rom, "nds") is None
    # Missing file -> no fingerprint -> miss, never raises.
    assert cache.get(tmp_path / "nope.gba", "gba") is None

    # Same size, different mtime_ns -> the fingerprint must miss.
    stat = rom.stat()
    monkeypatch.setattr(
        "vajsave.identity.cache.RomIdentityCache.fingerprint",
        staticmethod(lambda p: (stat.st_size, stat.st_mtime_ns + 1)),
    )
    assert cache.get(rom, "gba") is None


# --- write throttling (dirty + flush) ---------------------------------------


def test_rom_cache_put_defers_write_until_flush(tmp_path: Path):
    from vajsave.identity import GameIdentity
    from vajsave.identity.cache import RomIdentityCache

    rom = tmp_path / "Kirby.gba"
    rom.write_bytes(make_gba_rom())
    cache_path = tmp_path / "cache.json"
    cache = RomIdentityCache(cache_path)
    identity = GameIdentity(identity_key="gba:sha1:aa", platform="gba", title="Kirby")

    cache.put(rom, "gba", identity)
    # The record is only in memory; nothing touches the disk until flush().
    assert not cache_path.exists()
    assert cache.dirty is True
    assert cache.get(rom, "gba").identity_key == "gba:sha1:aa"

    assert cache.flush() is True
    assert cache_path.is_file()
    assert cache.dirty is False
    # A clean cache never rewrites the file again.
    assert cache.flush() is False


def test_resolve_many_flushes_rom_cache_once(tmp_path: Path, monkeypatch):
    from vajsave.identity.cache import RomIdentityCache

    rom_dir = _rom_dir(tmp_path)
    saves = []
    for i in range(6):
        name = f"Game{i}"
        (rom_dir / f"{name}.gba").write_bytes(make_gba_rom(title=f"GAME{i}"))
        saves.append(entry("gba", name, tmp_path / "SAVER" / f"{name}.sav"))

    cache_path = tmp_path / "rom_cache.json"
    resolver = GameIdentityResolver(rom_dirs={"gba": [rom_dir]}, cache_path=cache_path)

    writes = []
    real_save = RomIdentityCache.save

    def counting_save(self):
        writes.append(1)
        return real_save(self)

    monkeypatch.setattr(RomIdentityCache, "save", counting_save)

    results = resolver.resolve_many(saves)
    assert all(r.is_resolved for r in results)
    # Six cold ROMs still cost exactly one atomic write, not six.
    assert writes == [1]
    assert cache_path.is_file()


def test_rom_cache_get_tolerates_bad_identity_payload(tmp_path: Path):
    from vajsave.identity.cache import RomIdentityCache

    rom = tmp_path / "Kirby.gba"
    rom.write_bytes(make_gba_rom())
    stat = rom.stat()
    cache = RomIdentityCache(tmp_path / "cache.json")
    cache._entries[cache.key_for(rom)] = {
        "platform": "gba",
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "identity": "not-a-dict",
    }
    assert cache.get(rom, "gba") is None


def test_rom_cache_load_rejects_non_mapping_payload(tmp_path: Path):
    from vajsave.identity.cache import RomIdentityCache

    cache_path = tmp_path / "cache.json"
    cache_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert RomIdentityCache(cache_path).all() == {}


def test_rom_cache_save_failure_returns_false(tmp_path: Path):
    from vajsave.identity import GameIdentity
    from vajsave.identity.cache import RomIdentityCache

    blocker = tmp_path / "blocker"
    blocker.write_text("file, not a directory", encoding="utf-8")
    cache = RomIdentityCache(blocker / "cache.json")
    cache._entries["k"] = {"platform": "gba", "identity": {}}
    assert cache.save() is False
    # A failed cache write must not break resolution.
    rom = tmp_path / "Kirby.gba"
    rom.write_bytes(make_gba_rom())
    cache.put(rom, "gba", GameIdentity(identity_key="gba:sha1:aa", platform="gba", title="K"))
    assert cache.get(rom, "gba").identity_key == "gba:sha1:aa"

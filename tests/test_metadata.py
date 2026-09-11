"""Metadata layer: libretro index parsing/lookup, cache and service.

The acceptance contract for this layer:

* a GBA/NDS digest resolves ``canonical_title`` + ``region`` from a complete
  local index, while unknown digests and any other platform return ``None``;
* the index is parsed once and then served from an in-memory map;
* a metadata cache hit never consults the provider, and every failure degrades.
"""

from __future__ import annotations

import json
from pathlib import Path

from vajsave.metadata import (
    GameMetadata,
    LibretroIndex,
    LibretroMetadataProvider,
    METADATA_CACHE_NAME,
    MetadataCache,
    MetadataService,
    bundled_libretro_dir,
    default_libretro_dirs,
    detect_platform,
    metadata_key,
)
from vajsave.metadata.libretro import _normalize_hex

SHA1_A = "a" * 40
SHA1_B = "b" * 40
CRC_A = "1a2b3c4d"
CRC_B = "0000abcd"


def _dat(name: str, games) -> str:
    """Build a Logiqx datafile. ``games`` is ``[(title, [(sha1, crc), ...])]``."""
    parts = [
        '<?xml version="1.0"?>',
        "<datafile>",
        f"  <header><name>{name}</name></header>",
    ]
    for title, roms in games:
        parts.append(f'  <game name="{title}">')
        parts.append(f"    <description>{title}</description>")
        for sha1, crc in roms:
            parts.append(
                f'    <rom name="{title}.bin" size="1024" crc="{crc}" sha1="{sha1}"/>'
            )
        parts.append("  </game>")
    parts.append("</datafile>")
    return "\n".join(parts)


def write_dat(path: Path, name: str, games) -> Path:
    path.write_text(_dat(name, games), encoding="utf-8")
    return path


# --- index parsing & lookup --------------------------------------------------


def test_index_resolves_by_sha1_and_crc(tmp_path: Path):
    dat = write_dat(
        tmp_path / "gba.dat",
        "Nintendo - Game Boy Advance",
        [("Pokemon - FireRed Version (USA)", [(SHA1_A, CRC_A)])],
    )
    index = LibretroIndex()
    assert index.load_file(dat) == 1

    by_sha1 = index.lookup(platform="gba", sha1=SHA1_A)
    by_crc = index.lookup(platform="gba", crc32=CRC_A)
    assert by_sha1 is not None and by_sha1.canonical_title == "Pokemon - FireRed Version (USA)"
    assert by_crc is not None and by_crc.canonical_title == by_sha1.canonical_title
    assert by_sha1.region == "USA"
    assert by_sha1.platform == "gba"
    assert by_sha1.source == "libretro"
    assert by_sha1.rom_sha1 == SHA1_A
    assert by_sha1.rom_crc32 == CRC_A


def test_index_sha1_preferred_over_crc(tmp_path: Path):
    dat = write_dat(
        tmp_path / "gba.dat",
        "Nintendo - Game Boy Advance",
        [
            ("Game A (USA)", [(SHA1_A, CRC_A)]),
            ("Game B (Japan)", [(SHA1_B, CRC_A)]),
        ],
    )
    index = LibretroIndex.from_paths([dat])
    found = index.lookup(platform="gba", sha1=SHA1_B, crc32=CRC_A)
    assert found is not None and found.canonical_title == "Game B (Japan)"
    assert found.region == "Japan"


def test_index_unknown_digest_is_none(tmp_path: Path):
    dat = write_dat(
        tmp_path / "gba.dat",
        "Nintendo - Game Boy Advance",
        [("Game A (USA)", [(SHA1_A, CRC_A)])],
    )
    index = LibretroIndex.from_paths([dat])
    assert index.lookup(platform="gba", sha1="c" * 40) is None
    assert index.lookup(platform="gba", crc32="deadbeef") is None
    assert index.lookup(platform="gba") is None


def test_index_other_platform_is_none(tmp_path: Path):
    dat = write_dat(
        tmp_path / "gba.dat",
        "Nintendo - Game Boy Advance",
        [("Game A (USA)", [(SHA1_A, CRC_A)])],
    )
    index = LibretroIndex.from_paths([dat])
    assert index.lookup(platform="psp", sha1=SHA1_A) is None
    assert index.lookup(platform="switch", crc32=CRC_A) is None
    assert index.lookup(platform="", sha1=SHA1_A) is None

    # A datafile for an unsupported platform contributes nothing at all.
    psp = write_dat(tmp_path / "psp.dat", "Sony - PlayStation Portable", [("Game", [(SHA1_B, CRC_B)])])
    unsupported = LibretroIndex.from_paths([psp])
    assert len(unsupported) == 0
    assert unsupported.lookup(platform="psp", sha1=SHA1_B) is None


def test_index_missing_and_malformed_files_do_not_raise(tmp_path: Path):
    assert LibretroIndex.from_paths([tmp_path / "nope.dat"]) is not None
    broken = tmp_path / "broken.dat"
    broken.write_text("<datafile><game name=", encoding="utf-8")
    index = LibretroIndex.from_paths([broken])
    assert len(index) == 0
    # Empty/odd roots also degrade.
    assert len(LibretroIndex.from_paths([tmp_path / "missing_dir"])) == 0


def test_load_directory_is_non_recursive_and_dedupes(tmp_path: Path):
    sub = tmp_path / "nested"
    sub.mkdir()
    write_dat(tmp_path / "gba.dat", "Nintendo - Game Boy Advance", [("A (USA)", [(SHA1_A, CRC_A)])])
    write_dat(sub / "nds.dat", "Nintendo - Nintendo DS", [("B (Japan)", [(SHA1_B, CRC_B)])])
    index = LibretroIndex()
    index.load_directory(tmp_path)
    assert len(index) == 1
    assert index.lookup(platform="gba", sha1=SHA1_A) is not None
    assert index.lookup(platform="nds", sha1=SHA1_B) is None


def test_load_file_explicit_platform_overrides_header(tmp_path: Path):
    dat = write_dat(tmp_path / "mystery.dat", "Unknown Collection", [("A (USA)", [(SHA1_A, CRC_A)])])
    assert len(LibretroIndex.from_paths([dat])) == 0
    explicit = LibretroIndex()
    assert explicit.load_file(dat, platform="nds") == 1
    assert explicit.lookup(platform="nds", sha1=SHA1_A) is not None


def test_detect_platform_markers():
    assert detect_platform("Nintendo - Game Boy Advance") == "gba"
    assert detect_platform("Nintendo - Nintendo DS") == "nds"
    assert detect_platform("gba.dat") == "gba"
    assert detect_platform(None, "", "Some Other System") is None


def test_normalize_hex_pads_and_rejects_bad_values():
    assert _normalize_hex("A1B2", 8) == "0000a1b2"
    assert _normalize_hex("0xA1B2", 8) == "0000a1b2"
    assert _normalize_hex("", 8) is None
    assert _normalize_hex("zz", 8) is None
    assert _normalize_hex(None, 8) is None


# --- value object ------------------------------------------------------------


def test_game_metadata_roundtrip_and_display_title():
    meta = GameMetadata(
        canonical_title="Game A (USA)",
        platform="gba",
        region="USA",
        rom_sha1=SHA1_A,
        rom_crc32=CRC_A,
    )
    data = meta.to_dict()
    restored = GameMetadata.from_dict(data)
    assert restored == meta
    assert restored.display_title == "Game A (USA)"
    # Optional fields are omitted when unset.
    assert "region" not in GameMetadata(canonical_title="X", platform="nds").to_dict()


# --- cache -------------------------------------------------------------------


def test_cache_roundtrip_by_sha1_and_crc(tmp_path: Path):
    cache = MetadataCache(tmp_path / METADATA_CACHE_NAME)
    cache.put(GameMetadata(canonical_title="Game A (USA)", platform="gba", region="USA", rom_sha1=SHA1_A, rom_crc32=CRC_A))
    assert cache.get("gba", sha1=SHA1_A).canonical_title == "Game A (USA)"
    assert cache.get("gba", crc32=CRC_A) is not None
    assert cache.get("gba", sha1=SHA1_B) is None
    assert cache.get("nds", sha1=SHA1_A) is None

    reloaded = MetadataCache(tmp_path / METADATA_CACHE_NAME)
    assert reloaded.get("gba", sha1=SHA1_A) is not None


def test_cache_corrupt_and_bad_records_degrade(tmp_path: Path):
    path = tmp_path / METADATA_CACHE_NAME
    path.write_text("{not json", encoding="utf-8")
    cache = MetadataCache(path)
    assert cache.all() == {}

    cache.put(GameMetadata(canonical_title="X", platform="gba", rom_sha1=SHA1_A))
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["entries"][metadata_key("gba", sha1=SHA1_A)] = "not-a-dict"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert MetadataCache(path).get("gba", sha1=SHA1_A) is None


def test_cache_without_path_never_writes(tmp_path: Path):
    cache = MetadataCache(None)
    cache.put(GameMetadata(canonical_title="X", platform="gba", rom_sha1=SHA1_A))
    assert cache.get("gba", sha1=SHA1_A) is not None
    assert cache.save() is False


# --- service -----------------------------------------------------------------


class CountingProvider:
    def __init__(self, result=None, exc=None):
        self.calls = 0
        self.result = result
        self.exc = exc

    def lookup(self, *, platform, sha1=None, crc32=None):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.result


def test_service_cache_hit_never_calls_provider(tmp_path: Path):
    cache = MetadataCache(tmp_path / METADATA_CACHE_NAME)
    cache.put(GameMetadata(canonical_title="Cached", platform="gba", rom_sha1=SHA1_A))
    provider = CountingProvider(result=GameMetadata(canonical_title="From provider", platform="gba", rom_sha1=SHA1_A))
    service = MetadataService(provider, cache)

    found = service.lookup("gba", sha1=SHA1_A)
    assert found.canonical_title == "Cached"
    assert provider.calls == 0


def test_service_cache_miss_calls_provider_and_caches(tmp_path: Path):
    cache = MetadataCache(tmp_path / METADATA_CACHE_NAME)
    provider = CountingProvider(result=GameMetadata(canonical_title="Game", platform="nds", rom_sha1=SHA1_B))
    service = MetadataService(provider, cache)

    assert service.lookup("nds", sha1=SHA1_B).canonical_title == "Game"
    assert provider.calls == 1
    # Second lookup is served from the cache written by the first.
    assert service.lookup("nds", sha1=SHA1_B).canonical_title == "Game"
    assert provider.calls == 1


def test_service_provider_exception_degrades(tmp_path: Path):
    service = MetadataService(CountingProvider(exc=RuntimeError("boom")), MetadataCache(tmp_path / "m.json"))
    assert service.lookup("gba", sha1=SHA1_A) is None


def test_service_requires_platform_and_digest():
    service = MetadataService(CountingProvider(result=GameMetadata(canonical_title="X", platform="gba")), MetadataCache())
    assert service.lookup("", sha1=SHA1_A) is None
    assert service.lookup("gba") is None


def test_service_cached_is_cache_only(tmp_path: Path):
    provider = CountingProvider()
    service = MetadataService(provider, MetadataCache(tmp_path / "m.json"))
    assert service.cached("gba", sha1=SHA1_A) is None
    assert provider.calls == 0


def test_service_for_identity(tmp_path: Path):
    class FakeIdentity:
        platform = "gba"
        rom_sha1 = SHA1_A
        rom_crc32 = CRC_A

    cache = MetadataCache(tmp_path / "m.json")
    cache.put(GameMetadata(canonical_title="Game", platform="gba", rom_sha1=SHA1_A))
    service = MetadataService(CountingProvider(), cache)
    assert service.for_identity(FakeIdentity()).canonical_title == "Game"
    assert service.for_identity(None) is None


def test_libretro_provider_loads_index_once(tmp_path: Path, monkeypatch):
    write_dat(tmp_path / "gba.dat", "Nintendo - Game Boy Advance", [("A (USA)", [(SHA1_A, CRC_A)])])
    calls = []
    real = LibretroIndex.from_paths.__func__

    def counting(cls, paths, **kwargs):
        calls.append(list(paths))
        return real(cls, paths, **kwargs)

    monkeypatch.setattr(LibretroIndex, "from_paths", classmethod(counting))
    provider = LibretroMetadataProvider([tmp_path])
    assert provider.loaded is False
    assert provider.lookup(platform="gba", sha1=SHA1_A) is not None
    assert provider.loaded is True
    assert provider.lookup(platform="gba", crc32=CRC_A) is not None
    assert len(calls) == 1


def test_libretro_provider_exception_degrades():
    class ExplodingProvider(LibretroMetadataProvider):
        def _ensure_index(self):
            raise RuntimeError("boom")

    assert ExplodingProvider([]).lookup(platform="gba", sha1=SHA1_A) is None


# --- paths -------------------------------------------------------------------


def test_default_libretro_dirs_order_and_dedupe(tmp_path: Path):
    configured = tmp_path / "custom"
    dirs = default_libretro_dirs(tmp_path / "lib", configured=[str(configured), str(configured)])
    assert dirs[0] == configured
    assert dirs[1] == tmp_path / "lib" / "metadata" / "libretro"
    assert dirs[-1] == bundled_libretro_dir()
    # No duplicates even when the configured dir repeats.
    assert len({str(d) for d in dirs}) == len(dirs)


def test_default_libretro_dirs_accepts_single_string(tmp_path: Path):
    dirs = default_libretro_dirs(None, configured=str(tmp_path / "a"))
    assert dirs[0] == tmp_path / "a"
    assert dirs[-1] == bundled_libretro_dir()

"""Metadata layer: index parsing/lookup, cache, provider and resolver.

The acceptance contract for this layer:

* the bundled compact index resolves real GBA/NDS digests offline, and a raw
  Logiqx/ClrMamePro DAT dropped next to the library resolves too;
* ``GameMetadata`` carries ``identity_key`` + ``external_ids`` and stays
  separate from ``GameIdentity``;
* ``GameMetadataResolver.resolve(identity)`` is cache-first and a cache hit
  never consults the provider;
* ``game_metadata.json`` is keyed by ``identity_key`` and every failure
  degrades instead of raising.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from vajsave.metadata import (
    GameMetadata,
    GameMetadataResolver,
    LibretroIndex,
    LibretroMetadataProvider,
    METADATA_CACHE_NAME,
    MetadataCache,
    bundled_libretro_dir,
    default_libretro_dirs,
    detect_platform,
    external_ids_for,
)
from vajsave.metadata.libretro import INDEX_FORMAT, _normalize_hex

SHA1_A = "a" * 40
SHA1_B = "b" * 40
CRC_A = "1a2b3c4d"
CRC_B = "0000abcd"

# Real, well-known No-Intro digests (also present in the bundled index).
EMERALD_SHA1 = "f3ae088181bf583e55daf962a92bb46f4f1d07b7"
EMERALD_CRC = "1f1c08fb"
HEARTGOLD_SHA1 = "007d061e1abc8d9b56c6378c82fcfb3fc990adf3"
HEARTGOLD_CRC = "4723410a"

IDENTITY_KEY = f"gba:sha1:{SHA1_A}"


class FakeIdentity:
    def __init__(self, platform="gba", sha1=SHA1_A, crc=CRC_A, key=None, game_code=None):
        self.platform = platform
        self.rom_sha1 = sha1
        self.rom_crc32 = crc
        self.game_code = game_code
        self.identity_key = key if key is not None else (
            f"{platform}:sha1:{sha1}" if sha1 else f"{platform}:crc32:{crc}"
        )


def _dat(name: str, games) -> str:
    """Build a Logiqx datafile. ``games`` is ``[(title, [(sha1, crc), ...])]``."""
    parts = [
        '<?xml version="1.0"?>',
        '<datafile xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">',
        f"  <header><name>{name}</name><version>20260101-000000</version>",
        "    <clrmamepro forcenodump=\"required\"/>",
        "  </header>",
    ]
    for title, roms in games:
        parts.append(f'  <game name="{title}" id="42">')
        parts.append(f"    <description>{title}</description>")
        for sha1, crc in roms:
            parts.append(
                f'    <rom name="{title}.bin" size="1024" crc="{crc}" sha1="{sha1}" serial="BPEE"/>'
            )
        parts.append("  </game>")
    parts.append("</datafile>")
    return "\n".join(parts)


def write_dat(path: Path, name: str, games) -> Path:
    path.write_text(_dat(name, games), encoding="utf-8")
    return path


def write_compact(path: Path, platform: str, records) -> Path:
    path.write_text(
        json.dumps(
            {
                "format": INDEX_FORMAT,
                "format_version": 1,
                "platform": platform,
                "sources": [],
                "records": [list(r) for r in records],
            }
        ),
        encoding="utf-8",
    )
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
    assert by_sha1.identity_key == f"gba:sha1:{SHA1_A}"
    assert by_sha1.external_ids == {"serial": "BPEE", "nointro": "42"}


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

    psp = write_dat(tmp_path / "psp.dat", "Sony - PlayStation Portable", [("Game", [(SHA1_B, CRC_B)])])
    unsupported = LibretroIndex.from_paths([psp])
    assert len(unsupported) == 0
    assert unsupported.lookup(platform="psp", sha1=SHA1_B) is None


def test_index_missing_and_malformed_files_do_not_raise(tmp_path: Path):
    assert LibretroIndex.from_paths([tmp_path / "nope.dat"]) is not None
    broken = tmp_path / "broken.dat"
    broken.write_text("<datafile><game name=", encoding="utf-8")
    assert len(LibretroIndex.from_paths([broken])) == 0
    notjson = tmp_path / "broken.json"
    notjson.write_text("{not json", encoding="utf-8")
    assert len(LibretroIndex.from_paths([notjson])) == 0
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


def test_compact_json_index_loads(tmp_path: Path):
    path = write_compact(
        tmp_path / "gba.json",
        "gba",
        [(SHA1_A, CRC_A, "Game A (USA)", "BPEE", "42")],
    )
    index = LibretroIndex.from_paths([path])
    found = index.lookup(platform="gba", sha1=SHA1_A)
    assert found is not None
    assert found.canonical_title == "Game A (USA)"
    assert found.external_ids == {"serial": "BPEE", "nointro": "42"}


def test_compact_json_wrong_format_and_unsupported_platform_ignored(tmp_path: Path):
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"format": "other", "records": []}), encoding="utf-8")
    assert len(LibretroIndex.from_paths([wrong])) == 0
    unsupported = write_compact(tmp_path / "psp.json", "psp", [(SHA1_A, CRC_A, "Game", "", "")])
    assert len(LibretroIndex.from_paths([unsupported])) == 0


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


def test_external_ids_for_drops_blanks():
    assert external_ids_for("BPEE", "42") == {"serial": "BPEE", "nointro": "42"}
    assert external_ids_for("n/a", "") is None
    assert external_ids_for(None, None) is None


# --- serial (game code) fallback --------------------------------------------


def test_index_lookup_serial_normalizes_and_is_deterministic(tmp_path: Path):
    path = write_compact(
        tmp_path / "gba.json",
        "gba",
        [
            (SHA1_A, CRC_A, "Game A (Europe) (Rev 1)", "AX4P", "2"),
            (SHA1_B, CRC_B, "Game A (Europe)", "AX4P", "1"),
            ("c" * 40, "abcdef01", "Game A (Europe) (Virtual Console)", "AX4P", "3"),
        ],
    )
    index = LibretroIndex.from_paths([path])
    picked = index.lookup_serial(platform="gba", serial="ax4p")
    assert picked is not None
    # same-family variants collapse to one deterministic (base) release
    assert picked.canonical_title == "Game A (Europe)"
    assert index.lookup_serial(platform="gba", serial="AX4P") == picked
    assert index.lookup_serial(platform="gba", serial=" AX4P ") == picked


def test_index_lookup_serial_different_titles_is_none(tmp_path: Path):
    path = write_compact(
        tmp_path / "gba.json",
        "gba",
        [
            (SHA1_A, CRC_A, "Alpha (USA)", "BQ7E", "1"),
            (SHA1_B, CRC_B, "Beta (Japan)", "BQ7E", "2"),
        ],
    )
    index = LibretroIndex.from_paths([path])
    assert index.lookup_serial(platform="gba", serial="BQ7E") is None


def test_index_lookup_serial_missing_and_invalid_is_none(tmp_path: Path):
    path = write_compact(
        tmp_path / "gba.json",
        "gba",
        [
            (SHA1_A, CRC_A, "Game A (USA)", "BPEE", "1"),
            (SHA1_B, CRC_B, "Game B (USA)", "!none", "2"),
        ],
    )
    index = LibretroIndex.from_paths([path])
    assert index.lookup_serial(platform="gba", serial="2ATE") is None
    assert index.lookup_serial(platform="gba", serial="") is None
    assert index.lookup_serial(platform="gba", serial=None) is None
    assert index.lookup_serial(platform="gba", serial="!none") is None
    assert index.lookup_serial(platform="gba", serial="toolong") is None
    assert index.lookup_serial(platform="gba", serial="BPEE").canonical_title == "Game A (USA)"
    assert index.lookup_serial(platform="nds", serial="BPEE") is None
    assert index.lookup_serial(platform="psp", serial="BPEE") is None


def test_index_lookup_serial_prefers_retail_over_beta(tmp_path: Path):
    path = write_compact(
        tmp_path / "gba.json",
        "gba",
        [
            (SHA1_A, CRC_A, "Game A (Beta)", "Z9ZQ", "1"),
            (SHA1_B, CRC_B, "Game A (USA, Europe)", "Z9ZQ", "2"),
        ],
    )
    index = LibretroIndex.from_paths([path])
    # The retail build wins over a same-family beta even though it carries more tags.
    assert index.lookup_serial(platform="gba", serial="Z9ZQ").canonical_title == (
        "Game A (USA, Europe)"
    )


def test_index_lookup_serial_does_not_cross_platforms(tmp_path: Path):
    write_compact(tmp_path / "gba.json", "gba", [(SHA1_A, CRC_A, "GBA Game (USA)", "Z9ZQ", "1")])
    write_compact(tmp_path / "nds.json", "nds", [(SHA1_B, CRC_B, "NDS Game (Japan)", "Z9ZQ", "2")])
    index = LibretroIndex.from_paths([tmp_path])
    assert index.lookup_serial(platform="gba", serial="Z9ZQ").canonical_title == "GBA Game (USA)"
    # The serial fallback is GBA-only by design, even with an NDS record loaded.
    assert index.lookup_serial(platform="nds", serial="Z9ZQ") is None


# --- bundled real index ------------------------------------------------------


def test_bundled_index_resolves_real_gba_hash_offline():
    # The *default* provider (no explicit dirs) resolves from the bundled index.
    provider = LibretroMetadataProvider()
    identity = FakeIdentity(platform="gba", sha1=EMERALD_SHA1, crc=EMERALD_CRC)
    metadata = provider.resolve(identity)
    assert metadata is not None
    assert metadata.canonical_title == "Pokemon - Emerald Version (USA, Europe)"
    assert metadata.region == "USA"
    assert metadata.platform == "gba"
    assert metadata.identity_key == identity.identity_key


def test_bundled_index_resolves_real_nds_hash_offline():
    provider = LibretroMetadataProvider(default_libretro_dirs())
    identity = FakeIdentity(platform="nds", sha1=HEARTGOLD_SHA1, crc=HEARTGOLD_CRC)
    metadata = provider.resolve(identity)
    assert metadata is not None
    assert metadata.canonical_title == "Pokemon - HeartGold Version (USA)"


def test_bundled_index_is_non_empty_and_parseable():
    index = LibretroIndex.from_paths([bundled_libretro_dir()])
    assert len(index) > 5000
    assert index.lookup(platform="gba", sha1=EMERALD_SHA1) is not None


def test_bundled_index_serial_fallback_resolves_real_game_code():
    provider = LibretroMetadataProvider()
    identity = FakeIdentity(sha1="c" * 40, crc="deadbeef", game_code="BPEE")
    metadata = provider.resolve(identity)
    assert metadata is not None
    assert metadata.canonical_title == "Pokemon - Emerald Version (USA, Europe)"
    assert metadata.identity_key == identity.identity_key


def test_bundled_index_serial_fallback_unknown_code_is_none():
    provider = LibretroMetadataProvider()
    identity = FakeIdentity(sha1="c" * 40, crc="deadbeef", game_code="2ATE")
    assert provider.resolve(identity) is None


# --- value object ------------------------------------------------------------


def test_game_metadata_roundtrip_and_display_title():
    meta = GameMetadata(
        identity_key=IDENTITY_KEY,
        canonical_title="Game A (USA)",
        platform="gba",
        region="USA",
        external_ids={"serial": "BPEE"},
    )
    data = meta.to_dict()
    assert data["identity_key"] == IDENTITY_KEY
    assert data["external_ids"] == {"serial": "BPEE"}
    restored = GameMetadata.from_dict(data)
    assert restored == meta
    assert restored.display_title == "Game A (USA)"
    # Optional fields are omitted when unset.
    plain = GameMetadata(identity_key=IDENTITY_KEY, canonical_title="X", platform="nds").to_dict()
    assert "region" not in plain and "external_ids" not in plain


# --- cache -------------------------------------------------------------------


def test_cache_roundtrip_keyed_by_identity(tmp_path: Path):
    cache = MetadataCache(tmp_path / METADATA_CACHE_NAME)
    cache.put(GameMetadata(identity_key=IDENTITY_KEY, canonical_title="Game A (USA)", platform="gba", region="USA"))
    assert cache.get(IDENTITY_KEY).canonical_title == "Game A (USA)"
    assert cache.get("gba:sha1:" + "z" * 40) is None
    assert cache.get(None) is None
    assert cache.get("") is None

    reloaded = MetadataCache(tmp_path / METADATA_CACHE_NAME)
    assert reloaded.get(IDENTITY_KEY) is not None
    # The file is named ``game_metadata.json`` as the contract requires.
    assert (tmp_path / METADATA_CACHE_NAME).name == "game_metadata.json"


def test_cache_put_without_identity_key_is_noop(tmp_path: Path):
    cache = MetadataCache(tmp_path / METADATA_CACHE_NAME)
    assert cache.put(GameMetadata(identity_key="", canonical_title="X", platform="gba")) is False
    assert cache.all() == {}


def test_cache_put_is_idempotent(tmp_path: Path):
    cache = MetadataCache(tmp_path / METADATA_CACHE_NAME)
    meta = GameMetadata(identity_key=IDENTITY_KEY, canonical_title="X", platform="gba")
    assert cache.put(meta) is True
    assert cache.put(meta) is False


def test_cache_write_failure_degrades(tmp_path: Path):
    # ``covers.json`` is a file, so the cache path's parent can never be made.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    cache = MetadataCache(blocker / "game_metadata.json")
    assert cache.put(GameMetadata(identity_key=IDENTITY_KEY, canonical_title="X", platform="gba")) is False
    assert cache.get(IDENTITY_KEY) is not None  # in-memory value still usable


def test_cache_load_rejects_non_mapping_entries(tmp_path: Path):
    path = tmp_path / METADATA_CACHE_NAME
    path.write_text(json.dumps({"version": 1, "entries": []}), encoding="utf-8")
    assert MetadataCache(path).all() == {}


def test_cache_corrupt_and_bad_records_degrade(tmp_path: Path):
    path = tmp_path / METADATA_CACHE_NAME
    path.write_text("{not json", encoding="utf-8")
    cache = MetadataCache(path)
    assert cache.all() == {}

    cache.put(GameMetadata(identity_key=IDENTITY_KEY, canonical_title="X", platform="gba"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["entries"][IDENTITY_KEY] = "not-a-dict"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert MetadataCache(path).get(IDENTITY_KEY) is None


def test_cache_without_path_never_writes(tmp_path: Path):
    cache = MetadataCache(None)
    cache.put(GameMetadata(identity_key=IDENTITY_KEY, canonical_title="X", platform="gba"))
    assert cache.get(IDENTITY_KEY) is not None
    assert cache.save() is False


def test_cache_is_thread_safe_under_concurrent_puts(tmp_path: Path):
    cache = MetadataCache(tmp_path / METADATA_CACHE_NAME)

    def worker(index: int) -> None:
        for j in range(20):
            cache.put(
                GameMetadata(
                    identity_key=f"gba:sha1:{index:02d}{j:038d}",
                    canonical_title=f"G{index}-{j}",
                    platform="gba",
                )
            )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(cache.all()) == 80
    assert len(MetadataCache(tmp_path / METADATA_CACHE_NAME).all()) == 80


# --- provider / resolver -----------------------------------------------------


class CountingProvider:
    def __init__(self, result=None, exc=None):
        self.calls = 0
        self.result = result
        self.exc = exc

    def resolve(self, identity):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.result


def test_resolver_cache_hit_never_calls_provider(tmp_path: Path):
    identity = FakeIdentity()
    cache = MetadataCache(tmp_path / METADATA_CACHE_NAME)
    cache.put(GameMetadata(identity_key=identity.identity_key, canonical_title="Cached", platform="gba"))
    provider = CountingProvider(result=GameMetadata(identity_key=identity.identity_key, canonical_title="From provider", platform="gba"))
    resolver = GameMetadataResolver(provider, cache)

    found = resolver.resolve(identity)
    assert found.canonical_title == "Cached"
    assert provider.calls == 0
    assert resolver.cached(identity).canonical_title == "Cached"


def test_resolver_cache_miss_calls_provider_and_caches(tmp_path: Path):
    identity = FakeIdentity(platform="nds", sha1=SHA1_B, crc=CRC_B)
    cache = MetadataCache(tmp_path / METADATA_CACHE_NAME)
    provider = CountingProvider(result=GameMetadata(identity_key=identity.identity_key, canonical_title="Game", platform="nds"))
    resolver = GameMetadataResolver(provider, cache)

    assert resolver.resolve(identity).canonical_title == "Game"
    assert provider.calls == 1
    assert resolver.resolve(identity).canonical_title == "Game"
    assert provider.calls == 1


def test_resolver_provider_exception_degrades(tmp_path: Path):
    resolver = GameMetadataResolver(CountingProvider(exc=RuntimeError("boom")), MetadataCache(tmp_path / "m.json"))
    assert resolver.resolve(FakeIdentity()) is None


def test_resolver_without_provider_and_none_identity(tmp_path: Path):
    resolver = GameMetadataResolver(None, MetadataCache(tmp_path / "m.json"))
    assert resolver.resolve(FakeIdentity()) is None
    assert resolver.resolve(None) is None
    assert resolver.cached(None) is None


def test_resolver_cached_without_key_is_none(tmp_path: Path):
    resolver = GameMetadataResolver(CountingProvider(), MetadataCache(tmp_path / "m.json"))

    class NoKey:
        identity_key = ""
        platform = "gba"
        rom_sha1 = SHA1_A
        rom_crc32 = CRC_A

    assert resolver.cached(NoKey()) is None


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
    identity = FakeIdentity()
    assert provider.resolve(identity) is not None
    assert provider.loaded is True
    assert provider.resolve(identity) is not None
    assert len(calls) == 1
    assert provider.dirs == [tmp_path]


def test_libretro_provider_edge_cases():
    provider = LibretroMetadataProvider([])
    assert provider.resolve(None) is None
    assert provider.resolve(FakeIdentity(platform="psp", sha1=SHA1_A)) is None
    assert provider.resolve(FakeIdentity(sha1=None, crc=None)) is None


def test_provider_digest_hit_wins_over_serial(tmp_path: Path):
    path = write_compact(
        tmp_path / "gba.json",
        "gba",
        [
            (SHA1_A, CRC_A, "Digest Game (USA)", "AX4P", "1"),
            (SHA1_B, CRC_B, "Serial Game (USA)", "BQ7E", "2"),
        ],
    )
    provider = LibretroMetadataProvider([tmp_path])
    identity = FakeIdentity(sha1=SHA1_A, crc=CRC_A, game_code="BQ7E")
    found = provider.resolve(identity)
    assert found is not None
    assert found.canonical_title == "Digest Game (USA)"
    assert found.identity_key == identity.identity_key


def test_provider_digest_miss_falls_back_to_game_code(tmp_path: Path):
    path = write_compact(
        tmp_path / "gba.json",
        "gba",
        [(SHA1_B, CRC_B, "Serial Game (USA)", "AX4P", "2")],
    )
    provider = LibretroMetadataProvider([tmp_path])
    identity = FakeIdentity(sha1=SHA1_A, crc=CRC_A, game_code="ax4p")
    found = provider.resolve(identity)
    assert found is not None
    assert found.canonical_title == "Serial Game (USA)"
    assert found.identity_key == identity.identity_key


def test_provider_digest_miss_ambiguous_serial_is_none(tmp_path: Path):
    path = write_compact(
        tmp_path / "gba.json",
        "gba",
        [
            (SHA1_A, CRC_A, "Alpha (USA)", "BQ7E", "1"),
            (SHA1_B, CRC_B, "Beta (Japan)", "BQ7E", "2"),
        ],
    )
    provider = LibretroMetadataProvider([tmp_path])
    identity = FakeIdentity(sha1="c" * 40, crc="deadbeef", game_code="BQ7E")
    assert provider.resolve(identity) is None


def test_provider_digest_miss_no_record_is_none(tmp_path: Path):
    path = write_compact(
        tmp_path / "gba.json",
        "gba",
        [(SHA1_A, CRC_A, "Game A (USA)", "BPEE", "1")],
    )
    provider = LibretroMetadataProvider([tmp_path])
    identity = FakeIdentity(sha1="c" * 40, crc="deadbeef", game_code="2ATE")
    assert provider.resolve(identity) is None


def test_provider_digest_miss_without_game_code_is_none(tmp_path: Path):
    # A title-like identity must never be guessed from the filename/title.
    write_compact(
        tmp_path / "gba.json",
        "gba",
        [(SHA1_A, CRC_A, "Apotris - Rhythm Game (USA)", "Z9ZQ", "1")],
    )
    provider = LibretroMetadataProvider([tmp_path])
    identity = FakeIdentity(sha1="c" * 40, crc="deadbeef", game_code=None)
    assert provider.resolve(identity) is None


def test_provider_serial_only_identity_resolves(tmp_path: Path):
    write_compact(
        tmp_path / "gba.json",
        "gba",
        [(SHA1_A, CRC_A, "Game A (USA)", "Z9ZQ", "1")],
    )
    provider = LibretroMetadataProvider([tmp_path])
    identity = FakeIdentity(sha1=None, crc=None, game_code="Z9ZQ")
    found = provider.resolve(identity)
    assert found is not None
    assert found.canonical_title == "Game A (USA)"
    assert found.identity_key == identity.identity_key


def test_resolver_caches_serial_fallback_under_identity_key(tmp_path: Path):
    write_compact(
        tmp_path / "gba.json",
        "gba",
        [(SHA1_B, CRC_B, "Serial Game (USA)", "AX4P", "2")],
    )
    provider = LibretroMetadataProvider([tmp_path])
    cache = MetadataCache(tmp_path / METADATA_CACHE_NAME)
    resolver = GameMetadataResolver(provider, cache)
    identity = FakeIdentity(sha1=SHA1_A, crc=CRC_A, game_code="AX4P")

    found = resolver.resolve(identity)
    assert found is not None
    assert found.identity_key == identity.identity_key
    assert resolver.cached(identity).canonical_title == "Serial Game (USA)"
    # A cache hit must not consult the provider again.
    assert resolver.resolve(identity).canonical_title == "Serial Game (USA)"


def test_libretro_provider_exception_degrades():
    class ExplodingProvider(LibretroMetadataProvider):
        def _ensure_index(self):
            raise RuntimeError("boom")

    assert ExplodingProvider([]).resolve(FakeIdentity()) is None


# --- paths -------------------------------------------------------------------


def test_default_libretro_dirs_order_and_dedupe(tmp_path: Path):
    configured = tmp_path / "custom"
    dirs = default_libretro_dirs(tmp_path / "lib", configured=[str(configured), str(configured)])
    assert dirs[0] == configured
    assert dirs[1] == tmp_path / "lib" / "metadata" / "libretro"
    assert dirs[-1] == bundled_libretro_dir()
    assert len({str(d) for d in dirs}) == len(dirs)


def test_default_libretro_dirs_accepts_single_string(tmp_path: Path):
    dirs = default_libretro_dirs(None, configured=str(tmp_path / "a"))
    assert dirs[0] == tmp_path / "a"
    assert dirs[-1] == bundled_libretro_dir()

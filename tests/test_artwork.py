"""Metadata-driven artwork: providers, downloader, cache, service and loader.

Pins the acceptance contract:

* official libretro filename/URL rules (reserved chars incl. the double quote);
* ``covers/<platform>/<identity-hash>.png`` with a manifest carrying the exact
  required fields and zero-network hits;
* 404 / timeout / offline / partial / invalid-image degrade cleanly and never
  pollute the cache;
* ``user > downloaded > embedded > placeholder`` fallback order;
* background work is deduplicated per key and delivered through ``schedule``.
"""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from pathlib import Path

from PIL import Image

from vajsave.artwork import (
    COVER_CACHE_DIR,
    MANIFEST_FIELDS,
    Artwork,
    ArtworkDownloader,
    ArtworkLoader,
    ArtworkService,
    CoverCache,
    LibretroThumbnailProvider,
    PLACEHOLDER,
    resolve_artwork,
    sanitize_libretro_filename,
    SOURCE_DOWNLOADED,
    SOURCE_EMBEDDED,
    SOURCE_PLACEHOLDER,
    SOURCE_USER,
)
from vajsave.artwork.providers import ArtworkProvider, LIBRETRO_SYSTEM_NAMES
from vajsave.metadata import GameMetadata
from vajsave.models import SaveEntry


def png_bytes(size=(4, 4), color=(200, 30, 30, 255)) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def make_entry(tmp_path: Path, platform="gba", name="Apotris", *, cover_path=None) -> SaveEntry:
    save = tmp_path / "SAVEGAME" / f"{name}.sav"
    save.parent.mkdir(parents=True, exist_ok=True)
    save.write_bytes(b"save")
    return SaveEntry(
        platform=platform,
        source_id=f"{platform}_test",
        display_name=name,
        path=str(save),
        cover_path=cover_path,
    )


def make_metadata(platform="gba", title="Apotris (USA)", *, key=None) -> GameMetadata:
    return GameMetadata(
        identity_key=key or f"{platform}:sha1:" + "a" * 40,
        platform=platform,
        canonical_title=title,
        region="USA",
    )


class FakeResponse:
    def __init__(self, data=b"", status=200, content_length=None):
        self._data = data
        self.status = status
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.closed = False

    def read(self, n=-1):
        if n is None or n < 0:
            return self._data
        return self._data[:n]

    def close(self):
        self.closed = True


# --- providers ---------------------------------------------------------------


def test_sanitize_libretro_filename_rules():
    assert sanitize_libretro_filename("Pokemon - FireRed Version (USA)") == (
        "Pokemon - FireRed Version (USA)"
    )
    assert sanitize_libretro_filename("A&B/C:D") == "A_B_C_D"
    assert sanitize_libretro_filename('Spider-Man "The Movie"') == "Spider-Man _The Movie_"
    assert sanitize_libretro_filename("back`tick\\slash|pipe") == "back_tick_slash_pipe"
    assert sanitize_libretro_filename("Trailing.") == "Trailing"
    assert sanitize_libretro_filename("") is None
    assert sanitize_libretro_filename("   ") is None


def test_libretro_provider_find_cover_and_url_rules():
    provider = LibretroThumbnailProvider()
    assert provider.supports("gba") and provider.supports("NDS")
    assert not provider.supports("psp")

    artwork = provider.find_cover(make_metadata("gba", "Pokemon - FireRed Version (USA)"))
    assert isinstance(artwork, Artwork)
    assert artwork.filename == "Pokemon - FireRed Version (USA)"
    assert artwork.provider == "libretro"
    assert artwork.platform == "gba"
    assert artwork.url == (
        "https://thumbnails.libretro.com/"
        + LIBRETRO_SYSTEM_NAMES["gba"].replace(" ", "%20")
        + "/Named_Boxarts/Pokemon%20-%20FireRed%20Version%20(USA).png"
    )
    # A double quote is replaced, never percent-encoded into the URL.
    quoted = provider.find_cover(make_metadata("gba", 'Game "X" (USA)'))
    assert quoted is not None and '"' not in quoted.url and "%22" not in quoted.url
    assert provider.find_cover(make_metadata("psp", "Anything")) is None
    assert provider.find_cover(make_metadata("gba", "")) is None
    assert provider.find_cover(None) is None


def test_base_provider_is_inert():
    base = ArtworkProvider()
    assert base.supports("gba") is False
    assert base.find_cover(make_metadata()) is None
    assert base.cover_for("gba", "Game") is None
    assert base.ref_for("gba", "Game") is None


def test_libretro_provider_custom_base_url():
    provider = LibretroThumbnailProvider("https://example.test/art/")
    artwork = provider.cover_for("nds", "Game (Japan)")
    assert artwork.url == (
        "https://example.test/art/Nintendo%20-%20Nintendo%20DS/Named_Boxarts/Game%20(Japan).png"
    )


# --- cover cache -------------------------------------------------------------


def test_cover_cache_store_lookup_and_manifest(tmp_path: Path):
    root = tmp_path / "lib" / COVER_CACHE_DIR
    cache = CoverCache(root)
    key = "gba:sha1:" + "a" * 40
    path = cache.store(
        "gba", key, png_bytes(), provider="libretro", canonical_title="Apotris (USA)", remote_url="https://x/y.png"
    )
    assert path is not None and path.is_file()
    assert path == root / "gba" / (cache.identity_hash(key) + ".png")

    reloaded = CoverCache(root)
    assert reloaded.lookup("gba", key) == path
    entry = reloaded.manifest()[key]
    for field in MANIFEST_FIELDS:
        assert field in entry
    assert entry["identity_key"] == key
    assert entry["provider"] == "libretro"
    assert entry["canonical_title"] == "Apotris (USA)"
    assert entry["remote_url"] == "https://x/y.png"
    assert entry["local_path"] == f"gba/{cache.identity_hash(key)}.png"


def test_cover_cache_rejects_invalid_and_oversized_data(tmp_path: Path):
    cache = CoverCache(tmp_path / "covers", max_bytes=64)
    assert cache.store("gba", "k", b"<html>not an image</html>") is None
    assert cache.store("gba", "k", b"") is None
    assert cache.store("gba", "k", None) is None
    assert cache.store("gba", "k", png_bytes((200, 200))) is None  # over max_bytes
    assert cache.manifest() == {}
    assert not (tmp_path / "covers" / "gba").exists()


def test_cover_cache_missing_file_is_not_a_hit(tmp_path: Path):
    cache = CoverCache(tmp_path / "covers")
    path = cache.store("gba", "k", png_bytes())
    path.unlink()
    assert cache.lookup("gba", "k") is None


def test_cover_cache_path_mismatch_is_not_a_hit(tmp_path: Path):
    cache = CoverCache(tmp_path / "covers")
    cache.store("gba", "k", png_bytes())
    # Same key but a different platform must not hit.
    assert cache.lookup("nds", "k") is None


def test_cover_cache_without_root_is_inert():
    cache = CoverCache(None)
    assert cache.path_for("gba", "k") is None
    assert cache.store("gba", "k", png_bytes()) is None
    assert cache.lookup("gba", "k") is None
    assert cache.save() is False
    assert cache.prune() == 0


def test_cover_cache_write_failure_degrades(tmp_path: Path):
    root = tmp_path / "covers"
    root.write_text("not a directory", encoding="utf-8")
    cache = CoverCache(root)
    assert cache.store("gba", "k", png_bytes()) is None
    assert cache.manifest() == {}


def test_cover_cache_corrupt_entry_without_local_path_is_not_a_hit(tmp_path: Path):
    root = tmp_path / "covers"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps({"version": 1, "entries": {"gba:sha1:x": {"identity_key": "gba:sha1:x", "platform": "gba"}}}),
        encoding="utf-8",
    )
    assert CoverCache(root).lookup("gba", "gba:sha1:x") is None


def test_cover_cache_corrupt_manifest_degrades(tmp_path: Path):
    root = tmp_path / "covers"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text("{not json", encoding="utf-8")
    assert CoverCache(root).manifest() == {}
    (root / "manifest.json").write_text("[]", encoding="utf-8")
    assert CoverCache(root).manifest() == {}


def test_cover_cache_prune_drops_vanished_entries(tmp_path: Path):
    cache = CoverCache(tmp_path / "covers")
    path = cache.store("gba", "k", png_bytes())
    path.unlink()
    assert cache.prune() == 1
    assert cache.manifest() == {}


# --- downloader --------------------------------------------------------------


def test_downloader_success_and_close():
    response = FakeResponse(png_bytes())
    downloader = ArtworkDownloader(urlopen=lambda url, timeout=None: response)
    data = downloader.fetch("https://x/a.png")
    assert data == response._data
    assert response.closed is True


def test_downloader_404_offline_and_timeout():
    def opener_exc(exc):
        def opener(url, timeout=None):
            raise exc
        return opener

    for exc in (
        urllib.error.HTTPError("https://x", 404, "Not Found", {}, None),
        urllib.error.URLError("offline"),
        TimeoutError("timed out"),
    ):
        assert ArtworkDownloader(urlopen=opener_exc(exc)).fetch("https://x/a.png") is None


def test_downloader_partial_and_oversize_and_non_200():
    partial = FakeResponse(b"abc", content_length=100)
    assert ArtworkDownloader(urlopen=lambda url, timeout=None: partial).fetch("u") is None

    big = FakeResponse(b"x" * 50)
    assert ArtworkDownloader(urlopen=lambda url, timeout=None: big, max_bytes=10).fetch("u") is None

    not_found = FakeResponse(b"x", status=500)
    assert ArtworkDownloader(urlopen=lambda url, timeout=None: not_found).fetch("u") is None

    empty = FakeResponse(b"")
    assert ArtworkDownloader(urlopen=lambda url, timeout=None: empty).fetch("u") is None

    assert ArtworkDownloader().fetch(None) is None


def test_downloader_read_failure_returns_none():
    class Broken(FakeResponse):
        def read(self, n=-1):
            raise OSError("connection reset")

    assert ArtworkDownloader(urlopen=lambda url, timeout=None: Broken()).fetch("u") is None


# --- service: fallback order -------------------------------------------------


def test_resolve_artwork_fallback_order(tmp_path: Path):
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, cover_path=None)
    key = "gba:sha1:" + "a" * 40

    assert resolve_artwork(entry, library, cache=cache, identity_key=key).source == SOURCE_PLACEHOLDER

    embedded = tmp_path / "icon.png"
    embedded.write_bytes(png_bytes())
    entry.cover_path = str(embedded)
    assert resolve_artwork(entry, library, cache=cache, identity_key=key).source == SOURCE_EMBEDDED

    cache.store("gba", key, png_bytes())
    assert resolve_artwork(entry, library, cache=cache, identity_key=key).source == SOURCE_DOWNLOADED

    user_dir = library / "covers" / "gba"
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "Apotris.png").write_bytes(png_bytes())
    assert resolve_artwork(entry, library, cache=cache, identity_key=key).source == SOURCE_USER


def test_resolve_artwork_none_entry_is_placeholder(tmp_path: Path):
    assert resolve_artwork(None, tmp_path).source == SOURCE_PLACEHOLDER


def test_ensure_downloaded_cache_hit_is_zero_network(tmp_path: Path):
    cache = CoverCache(tmp_path / "covers")
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        return FakeResponse(png_bytes())

    service = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener))
    metadata = make_metadata("gba", "Apotris")
    key = metadata.identity_key
    first = service.ensure_downloaded(metadata, identity_key=key)
    assert first is not None and len(calls) == 1
    second = service.ensure_downloaded(metadata, identity_key=key)
    assert second == first and len(calls) == 1
    entry = cache.manifest()[key]
    assert entry["canonical_title"] == "Apotris"


def test_ensure_downloaded_unsupported_platform_and_missing_key(tmp_path: Path):
    service = ArtworkService(cache=CoverCache(tmp_path / "covers"))
    assert service.ensure_downloaded(make_metadata("psp", "Game"), identity_key="psp:x") is None
    assert service.ensure_downloaded(make_metadata(), identity_key=None) is None
    assert service.ensure_downloaded(None, identity_key="gba:x") is None


def test_ensure_downloaded_failure_paths_do_not_pollute_cache(tmp_path: Path):
    cache = CoverCache(tmp_path / "covers")
    key = "gba:sha1:" + "a" * 40
    metadata = make_metadata(key=key)

    def opener_exc(exc):
        def opener(url, timeout=None):
            raise exc
        return opener

    offline = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener_exc(urllib.error.URLError("x"))))
    assert offline.ensure_downloaded(metadata, identity_key=key) is None

    invalid = ArtworkService(
        cache=cache,
        downloader=ArtworkDownloader(urlopen=lambda url, timeout=None: FakeResponse(b"<html>")),
    )
    assert invalid.ensure_downloaded(metadata, identity_key=key) is None
    assert cache.lookup("gba", key) is None


def test_ensure_cover_full_fallback_order(tmp_path: Path):
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path)
    metadata = make_metadata(key="gba:sha1:" + "a" * 40)
    key = metadata.identity_key

    embedded = tmp_path / "icon.png"
    embedded.write_bytes(png_bytes())
    entry.cover_path = str(embedded)
    failing = ArtworkService(
        cache=cache,
        downloader=ArtworkDownloader(urlopen=lambda url, timeout=None: FakeResponse(b"")),
    )
    assert failing.ensure_cover(
        entry, metadata=metadata, identity_key=key, library_root=library
    ).source == SOURCE_EMBEDDED

    working = ArtworkService(
        cache=cache,
        downloader=ArtworkDownloader(urlopen=lambda url, timeout=None: FakeResponse(png_bytes())),
    )
    assert working.ensure_cover(
        entry, metadata=metadata, identity_key=key, library_root=library
    ).source == SOURCE_DOWNLOADED

    bare = make_entry(tmp_path, name="Bare")
    assert failing.ensure_cover(
        bare, metadata=make_metadata(key="gba:sha1:zz"), identity_key="gba:sha1:zz", library_root=library
    ).source == SOURCE_PLACEHOLDER


def test_ensure_cover_user_local_skips_network(tmp_path: Path):
    library = tmp_path / "lib"
    user_dir = library / "covers" / "gba"
    user_dir.mkdir(parents=True)
    (user_dir / "Apotris.png").write_bytes(png_bytes())
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        return FakeResponse(png_bytes())

    service = ArtworkService(cache=CoverCache(library / COVER_CACHE_DIR), downloader=ArtworkDownloader(urlopen=opener))
    entry = make_entry(tmp_path)
    resolution = service.ensure_cover(
        entry, metadata=make_metadata(), identity_key="gba:sha1:x", library_root=library
    )
    assert resolution.source == SOURCE_USER
    assert calls == []


def test_service_cover_for_falls_through_bad_provider():
    class Exploding(ArtworkProvider):
        name = "boom"
        def supports(self, platform):
            return True
        def find_cover(self, metadata):
            raise RuntimeError("boom")

    service = ArtworkService(providers=[Exploding(), LibretroThumbnailProvider()])
    artwork = service.cover_for(make_metadata("gba", "Apotris"))
    assert artwork is not None and artwork.provider == "libretro"
    # ``ref_for`` is the platform+title compatibility entry point.
    assert service.ref_for("gba", "Apotris").provider == "libretro"
    assert service.ref_for("psp", "Anything") is None
    assert service.ref_for("gba", "") is None


# --- loader ------------------------------------------------------------------


class ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)

    def shutdown(self, wait=False, cancel_futures=False):
        pass


def test_loader_dedupes_inflight_and_delivers_via_schedule():
    scheduled = []
    loader = ArtworkLoader(scheduled.append, executor=ImmediateExecutor())
    task_calls = []
    results = []

    assert loader.submit("k", lambda: task_calls.append(1) or "first", results.append) is True
    assert loader.is_inflight("k") is True
    assert loader.submit("k", lambda: task_calls.append(2) or "second", results.append) is False

    assert task_calls == [1]
    for deliver in scheduled:
        deliver()
    assert results == ["first", "first"]
    assert loader.is_inflight("k") is False


def test_loader_task_failure_delivers_none():
    scheduled = []
    loader = ArtworkLoader(scheduled.append, executor=ImmediateExecutor())
    results = []

    def boom():
        raise RuntimeError("nope")

    loader.submit("k", boom, results.append)
    for deliver in scheduled:
        deliver()
    assert results == [None]


def test_loader_shutdown_blocks_new_submissions():
    loader = ArtworkLoader(lambda fn: fn(), executor=ImmediateExecutor())
    loader.submit("a", lambda: 1, lambda r: None)
    loader.shutdown()
    assert loader.submit("b", lambda: 2, lambda r: None) is False


def test_loader_broken_callback_is_isolated():
    scheduled = []
    loader = ArtworkLoader(scheduled.append, executor=ImmediateExecutor())
    seen = []

    def bad(_):
        raise RuntimeError("cb")

    loader.submit("k", lambda: "v", bad)
    loader.submit("k", lambda: "v", seen.append)
    for deliver in scheduled:
        deliver()
    assert seen == ["v"]


def test_placeholder_constant():
    assert PLACEHOLDER.path is None and PLACEHOLDER.is_placeholder

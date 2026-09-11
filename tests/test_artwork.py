"""Metadata-driven artwork: providers, downloader, cache, service and loader.

Pins the acceptance contract:

* official libretro filename/URL rules;
* identity-hash cache naming with a complete manifest and zero-network hits;
* 404 / timeout / offline / partial / invalid-image degrade cleanly and never
  pollute the cache;
* ``user > downloaded > embedded > placeholder`` fallback order;
* background work is deduplicated per key and delivered through ``schedule``.
"""

from __future__ import annotations

import urllib.error
from io import BytesIO
from pathlib import Path

from PIL import Image

from vajsave.artwork import (
    COVER_CACHE_DIR,
    ArtworkDownloader,
    ArtworkLoader,
    ArtworkService,
    CoverCache,
    LibretroArtworkProvider,
    PLACEHOLDER,
    resolve_artwork,
    sanitize_libretro_filename,
    SOURCE_DOWNLOADED,
    SOURCE_EMBEDDED,
    SOURCE_PLACEHOLDER,
    SOURCE_USER,
)
from vajsave.artwork.providers import ArtworkProvider, LIBRETRO_SYSTEM_NAMES
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
    assert sanitize_libretro_filename("Trailing.") == "Trailing"
    assert sanitize_libretro_filename("") is None
    assert sanitize_libretro_filename("   ") is None


def test_libretro_provider_filename_and_url_rules():
    provider = LibretroArtworkProvider()
    assert provider.supports("gba") and provider.supports("NDS")
    assert not provider.supports("psp")
    ref = provider.ref_for("gba", "Pokemon - FireRed Version (USA)")
    assert ref is not None
    assert ref.filename == "Pokemon - FireRed Version (USA)"
    assert ref.url == (
        "https://thumbnails.libretro.com/"
        + LIBRETRO_SYSTEM_NAMES["gba"].replace(" ", "%20")
        + "/Named_Boxarts/Pokemon%20-%20FireRed%20Version%20(USA).png"
    )
    assert provider.ref_for("psp", "Anything") is None
    assert provider.ref_for("gba", "") is None


def test_base_provider_is_inert():
    base = ArtworkProvider()
    assert base.supports("gba") is False
    assert base.ref_for("gba", "Game") is None


def test_libretro_provider_custom_base_url():
    provider = LibretroArtworkProvider("https://example.test/art/")
    url = provider.url_for("nds", "Game (Japan)")
    assert url == (
        "https://example.test/art/Nintendo%20-%20Nintendo%20DS/Named_Boxarts/Game%20(Japan).png"
    )


# --- cover cache -------------------------------------------------------------


def test_cover_cache_store_lookup_and_manifest(tmp_path: Path):
    cache = CoverCache(tmp_path / COVER_CACHE_DIR)
    key = "gba:sha1:" + "a" * 40
    path = cache.store(key, png_bytes(), url="https://x/y.png", source="libretro")
    assert path is not None and path.is_file()
    assert path.name == cache.identity_hash(key) + ".png"
    # Valid hit round-trips across instances via the manifest.
    reloaded = CoverCache(tmp_path / COVER_CACHE_DIR)
    assert reloaded.lookup(key) == path
    entry = reloaded.manifest()[cache.identity_hash(key)]
    assert entry["identity_key"] == key
    assert entry["url"] == "https://x/y.png"
    assert entry["source"] == "libretro"
    assert entry["bytes"] == path.stat().st_size


def test_cover_cache_rejects_invalid_and_oversized_data(tmp_path: Path):
    cache = CoverCache(tmp_path / COVER_CACHE_DIR, max_bytes=64)
    assert cache.store("k", b"<html>not an image</html>") is None
    assert cache.store("k", b"") is None
    assert cache.store("k", None) is None
    assert cache.store("k", png_bytes((200, 200))) is None  # over max_bytes
    assert cache.manifest() == {}
    # Nothing was written for the rejected payloads.
    assert not (tmp_path / COVER_CACHE_DIR).exists() or not list(
        (tmp_path / COVER_CACHE_DIR).glob("*.png")
    )


def test_cover_cache_missing_file_is_not_a_hit(tmp_path: Path):
    cache = CoverCache(tmp_path / COVER_CACHE_DIR)
    path = cache.store("k", png_bytes())
    path.unlink()
    assert cache.lookup("k") is None


def test_cover_cache_without_root_is_inert():
    cache = CoverCache(None)
    assert cache.path_for("k") is None
    assert cache.store("k", png_bytes()) is None
    assert cache.lookup("k") is None
    assert cache.save() is False
    assert cache.prune() == 0


def test_cover_cache_corrupt_manifest_degrades(tmp_path: Path):
    root = tmp_path / COVER_CACHE_DIR
    root.mkdir(parents=True)
    (root / "manifest.json").write_text("{not json", encoding="utf-8")
    cache = CoverCache(root)
    assert cache.manifest() == {}


def test_cover_cache_prune_drops_vanished_entries(tmp_path: Path):
    cache = CoverCache(tmp_path / COVER_CACHE_DIR)
    path = cache.store("k", png_bytes())
    path.unlink()
    assert cache.prune() == 1
    assert cache.manifest() == {}


def test_cover_cache_load_rejects_non_mapping_manifest(tmp_path: Path):
    root = tmp_path / COVER_CACHE_DIR
    root.mkdir(parents=True)
    (root / "manifest.json").write_text("[]", encoding="utf-8")
    assert CoverCache(root).manifest() == {}


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

    # 4. placeholder
    assert resolve_artwork(entry, library, cache=cache, identity_key=key).source == SOURCE_PLACEHOLDER

    # 3. embedded
    embedded = tmp_path / "icon.png"
    embedded.write_bytes(png_bytes())
    entry.cover_path = str(embedded)
    assert resolve_artwork(entry, library, cache=cache, identity_key=key).source == SOURCE_EMBEDDED

    # 2. downloaded cache
    cache.store(key, png_bytes())
    assert resolve_artwork(entry, library, cache=cache, identity_key=key).source == SOURCE_DOWNLOADED

    # 1. user local
    user_dir = library / "covers" / "gba"
    user_dir.mkdir(parents=True)
    (user_dir / "Apotris.png").write_bytes(png_bytes())
    assert resolve_artwork(entry, library, cache=cache, identity_key=key).source == SOURCE_USER


def test_resolve_artwork_none_entry_is_placeholder(tmp_path: Path):
    assert resolve_artwork(None, tmp_path).source == SOURCE_PLACEHOLDER


def test_ensure_downloaded_cache_hit_is_zero_network(tmp_path: Path):
    cache = CoverCache(tmp_path / "cache")
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        return FakeResponse(png_bytes())

    service = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener))
    key = "gba:sha1:" + "a" * 40
    first = service.ensure_downloaded(platform="gba", title="Apotris", identity_key=key)
    assert first is not None and len(calls) == 1
    second = service.ensure_downloaded(platform="gba", title="Apotris", identity_key=key)
    assert second == first and len(calls) == 1


def test_ensure_downloaded_unsupported_platform_and_missing_key(tmp_path: Path):
    service = ArtworkService(cache=CoverCache(tmp_path / "cache"))
    assert service.ensure_downloaded(platform="psp", title="Game", identity_key="psp:x") is None
    assert service.ensure_downloaded(platform="gba", title="Game", identity_key=None) is None
    assert service.ensure_downloaded(platform="gba", title="", identity_key="gba:x") is None


def test_ensure_downloaded_failure_paths_do_not_pollute_cache(tmp_path: Path):
    cache = CoverCache(tmp_path / "cache")
    key = "gba:sha1:" + "a" * 40

    def opener_exc(exc):
        def opener(url, timeout=None):
            raise exc
        return opener

    offline = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener_exc(urllib.error.URLError("x"))))
    assert offline.ensure_downloaded(platform="gba", title="Apotris", identity_key=key) is None

    invalid = ArtworkService(
        cache=cache,
        downloader=ArtworkDownloader(urlopen=lambda url, timeout=None: FakeResponse(b"<html>")),
    )
    assert invalid.ensure_downloaded(platform="gba", title="Apotris", identity_key=key) is None
    assert cache.lookup(key) is None


def test_ensure_cover_full_fallback_order(tmp_path: Path):
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path)
    key = "gba:sha1:" + "a" * 40

    # No download -> embedded fallback.
    embedded = tmp_path / "icon.png"
    embedded.write_bytes(png_bytes())
    entry.cover_path = str(embedded)
    failing = ArtworkService(
        cache=cache,
        downloader=ArtworkDownloader(urlopen=lambda url, timeout=None: FakeResponse(b"")),
    )
    assert failing.ensure_cover(
        entry, platform="gba", title="Apotris", identity_key=key, library_root=library
    ).source == SOURCE_EMBEDDED

    # Download succeeds -> downloaded outranks embedded.
    working = ArtworkService(
        cache=cache,
        downloader=ArtworkDownloader(urlopen=lambda url, timeout=None: FakeResponse(png_bytes())),
    )
    assert working.ensure_cover(
        entry, platform="gba", title="Apotris", identity_key=key, library_root=library
    ).source == SOURCE_DOWNLOADED

    # Nothing anywhere -> placeholder (download also fails).
    bare = make_entry(tmp_path, name="Bare")
    assert failing.ensure_cover(
        bare, platform="gba", title="Bare", identity_key="gba:sha1:zz", library_root=library
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
        entry, platform="gba", title="Apotris", identity_key="gba:sha1:x", library_root=library
    )
    assert resolution.source == SOURCE_USER
    assert calls == []


def test_service_ref_for_falls_through_bad_provider(tmp_path: Path):
    class Exploding(ArtworkProvider):
        name = "boom"
        def supports(self, platform):
            return True
        def filename_for(self, title):
            raise RuntimeError("boom")
        def url_for(self, platform, filename):
            raise RuntimeError("boom")

    service = ArtworkService(providers=[Exploding(), LibretroArtworkProvider()])
    ref = service.ref_for("gba", "Apotris")
    assert ref is not None and ref.provider == "libretro"


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

    assert task_calls == [1]  # second submission did not start new work
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

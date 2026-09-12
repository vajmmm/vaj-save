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
from vajsave.artwork.providers import (
    ArtworkProvider,
    LIBRETRO_SYSTEM_NAMES,
    libretro_title_candidates,
)
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
    assert provider.supports("psp") and provider.supports("VITA")

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
    assert provider.find_cover(make_metadata("switch", "Anything")) is None
    assert provider.find_cover(make_metadata("gba", "")) is None
    assert provider.find_cover(None) is None


def test_libretro_system_names_include_psp_vita_and_3ds():
    assert LIBRETRO_SYSTEM_NAMES["psp"] == "Sony - PlayStation Portable"
    assert LIBRETRO_SYSTEM_NAMES["vita"] == "Sony - PlayStation Vita"
    assert LIBRETRO_SYSTEM_NAMES["3ds"] == "Nintendo - Nintendo 3DS"


def test_libretro_provider_psp_vita_and_3ds_boxart_urls():
    provider = LibretroThumbnailProvider()
    psp = provider.ref_for("psp", "Monster Hunter Portable 3rd")
    assert psp is not None
    assert psp.url == (
        "https://thumbnails.libretro.com/Sony%20-%20PlayStation%20Portable/"
        "Named_Boxarts/Monster%20Hunter%20Portable%203rd.png"
    )
    vita = provider.ref_for("vita", "Persona 4 Golden")
    assert vita is not None
    assert vita.url == (
        "https://thumbnails.libretro.com/Sony%20-%20PlayStation%20Vita/"
        "Named_Boxarts/Persona%204%20Golden.png"
    )
    ds3 = provider.ref_for("3ds", "Persona Q2 New Cinema Labyrinth")
    assert ds3 is not None
    assert ds3.url == (
        "https://thumbnails.libretro.com/Nintendo%20-%20Nintendo%203DS/"
        "Named_Boxarts/Persona%20Q2%20New%20Cinema%20Labyrinth.png"
    )


def test_libretro_title_candidates_checkpoint_shouting_and_regions():
    names = libretro_title_candidates("MARIO KART 7")
    assert names[0] == "MARIO KART 7"
    assert "Mario Kart 7" in names
    assert "Mario Kart 7 (USA)" in names
    # Double spaces / trailing ellipsis from Checkpoint folders.
    collapsed = libretro_title_candidates("Kid Icarus  Uprising")
    assert "Kid Icarus Uprising" in collapsed
    assert "Kid Icarus Uprising (USA)" in collapsed


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


def test_cover_cache_remove_deletes_file_and_manifest_entry(tmp_path: Path):
    root = tmp_path / "covers"
    cache = CoverCache(root)
    key = "gba:sha1:" + "a" * 40
    other = "gba:sha1:" + "b" * 40
    cover = cache.store("gba", key, png_bytes(), canonical_title="Apotris")
    other_cover = cache.store("gba", other, png_bytes(), canonical_title="Other")

    removed = cache.remove("gba", key)

    assert removed == [cover]
    assert not cover.exists()
    reloaded = CoverCache(root)
    assert key not in reloaded.manifest()
    assert reloaded.lookup("gba", key) is None
    assert other_cover.exists()
    assert other in reloaded.manifest()


def test_cover_cache_remove_platform_mismatch_keeps_cover(tmp_path: Path):
    root = tmp_path / "covers"
    cache = CoverCache(root)
    key = "gba:sha1:" + "a" * 40
    cover = cache.store("gba", key, png_bytes())

    assert cache.remove("nds", key) == []
    assert cover.exists()
    assert key in CoverCache(root).manifest()


def test_cover_cache_remove_unknown_key_is_noop(tmp_path: Path):
    cache = CoverCache(tmp_path / "covers")
    assert cache.remove("gba", "gba:sha1:" + "a" * 40) == []


def test_cover_cache_remove_without_root_is_inert():
    assert CoverCache(None).remove("gba", "k") == []


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
    assert service.ensure_downloaded(make_metadata("switch", "Game"), identity_key="switch:x") is None
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


def test_ensure_cover_for_title_downloads_psp_boxart(tmp_path: Path):
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, platform="psp", name="Monster Hunter")
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        return FakeResponse(png_bytes())

    service = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener))
    resolution = service.ensure_cover_for_title(
        entry,
        platform="psp",
        title="Monster Hunter Portable 3rd",
        identity_key="psp:ULJM05800",
        library_root=library,
    )
    assert resolution.source == SOURCE_DOWNLOADED
    assert calls and "Sony%20-%20PlayStation%20Portable" in calls[0]
    assert "Monster%20Hunter%20Portable%203rd" in calls[0]
    assert cache.manifest()["psp:ULJM05800"]["remote_url"] == calls[0]


def test_expand_3ds_checkpoint_id_and_eshop_name_cleanup():
    from vajsave.artwork.title_ids import (
        clean_eshop_name,
        expand_3ds_title_id,
        title_candidates_for_id,
        titles_from_3dsdb_lists,
    )

    assert expand_3ds_title_id("0x00306") == "0004000000030600"
    assert expand_3ds_title_id("0004000000030800") == "0004000000030800"
    assert (
        clean_eshop_name("The Legend of Zelda™: <br>A Link Between Worlds")
        == "The Legend of Zelda: A Link Between Worlds"
    )
    catalog = titles_from_3dsdb_lists(
        [
            (
                "USA",
                [
                    {
                        "Name": "The Legend of Zelda™: <br>A Link Between Worlds",
                        "TitleID": "00040000000EC300",
                    }
                ],
            )
        ]
    )
    names = title_candidates_for_id(catalog, "0x00EC3")
    assert "The Legend of Zelda: A Link Between Worlds" in names
    assert "The Legend of Zelda: A Link Between Worlds (USA)" in names


def test_ensure_cover_for_title_uses_3ds_title_id_when_folder_name_misses(tmp_path: Path):
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, platform="3ds", name="The Legend of Zelda")
    calls = []
    listing = json.dumps(
        [
            {
                "Name": "The Legend of Zelda™: <br>A Link Between Worlds",
                "TitleID": "00040000000EC300",
            }
        ]
    ).encode()

    def opener(url, timeout=None):
        calls.append(url)
        if "3dsdb" in url or "list_" in url:
            return FakeResponse(listing)
        if "A%20Link%20Between%20Worlds" in url:
            return FakeResponse(png_bytes())
        return FakeResponse(b"missing", status=404)

    service = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener))
    resolution = service.ensure_cover_for_title(
        entry,
        platform="3ds",
        title="The Legend of Zelda",
        identity_key="3ds:0x00EC3",
        library_root=library,
        title_id="0x00EC3",
    )
    assert resolution.source == SOURCE_DOWNLOADED
    assert any("A%20Link%20Between%20Worlds" in url for url in calls)


def test_ensure_cover_for_title_retries_region_suffix_after_404(tmp_path: Path):
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, platform="3ds", name="MK7")
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        if "Mario%20Kart%207%20(USA)" in url:
            return FakeResponse(png_bytes())
        return FakeResponse(b"missing", status=404)

    service = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener))
    resolution = service.ensure_cover_for_title(
        entry,
        platform="3ds",
        title="MARIO KART 7",
        identity_key="3ds:0x00306",
        library_root=library,
    )
    assert resolution.source == SOURCE_DOWNLOADED
    assert any("Mario%20Kart%207%20(USA)" in url for url in calls)
    assert len(calls) >= 2


def test_ensure_cover_for_title_embedded_icon_skips_network(tmp_path: Path):
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, platform="psp")
    icon = tmp_path / "ICON0.PNG"
    icon.write_bytes(png_bytes())
    entry.cover_path = str(icon)
    calls = []

    service = ArtworkService(
        cache=cache,
        downloader=ArtworkDownloader(
            urlopen=lambda url, timeout=None: calls.append(url) or FakeResponse(png_bytes())
        ),
    )
    resolution = service.ensure_cover_for_title(
        entry,
        platform="psp",
        title="Monster Hunter Portable 3rd",
        identity_key="psp:ULJM05800",
        library_root=library,
    )
    assert resolution.source == SOURCE_EMBEDDED
    assert Path(resolution.path) == icon
    assert calls == []


def test_ensure_cover_for_title_cache_hit_is_zero_network(tmp_path: Path):
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    key = "vita:PCSE00120"
    stored = cache.store("vita", key, png_bytes())
    entry = make_entry(tmp_path, platform="vita")
    calls = []

    service = ArtworkService(
        cache=cache,
        downloader=ArtworkDownloader(
            urlopen=lambda url, timeout=None: calls.append(url) or FakeResponse(png_bytes())
        ),
    )
    resolution = service.ensure_cover_for_title(
        entry,
        platform="vita",
        title="Persona 4 Golden",
        identity_key=key,
        library_root=library,
    )
    assert resolution.source == SOURCE_DOWNLOADED
    assert Path(resolution.path) == stored
    assert calls == []


def test_ensure_cover_for_title_user_local_skips_network(tmp_path: Path):
    library = tmp_path / "lib"
    user_dir = library / "covers" / "psp"
    user_dir.mkdir(parents=True)
    (user_dir / "Monster Hunter.png").write_bytes(png_bytes())
    entry = make_entry(tmp_path, platform="psp", name="Monster Hunter")
    calls = []

    service = ArtworkService(
        cache=CoverCache(library / COVER_CACHE_DIR),
        downloader=ArtworkDownloader(
            urlopen=lambda url, timeout=None: calls.append(url) or FakeResponse(png_bytes())
        ),
    )
    resolution = service.ensure_cover_for_title(
        entry,
        platform="psp",
        title="Monster Hunter Portable 3rd",
        identity_key="psp:ULJM05800",
        library_root=library,
    )
    assert resolution.source == SOURCE_USER
    assert calls == []


def test_ensure_cover_for_title_offline_failure_does_not_pollute_cache(tmp_path: Path):
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, platform="vita")
    key = "vita:PCSE00120"

    service = ArtworkService(
        cache=cache,
        downloader=ArtworkDownloader(
            urlopen=lambda url, timeout=None: (_ for _ in ()).throw(
                urllib.error.URLError("offline")
            )
        ),
    )
    resolution = service.ensure_cover_for_title(
        entry,
        platform="vita",
        title="Persona 4 Golden",
        identity_key=key,
        library_root=library,
    )
    assert resolution.source == SOURCE_PLACEHOLDER
    assert cache.lookup("vita", key) is None


def test_ensure_cover_for_title_unsupported_platform_and_no_title(tmp_path: Path):
    library = tmp_path / "lib"
    service = ArtworkService(
        cache=CoverCache(library / COVER_CACHE_DIR),
        downloader=ArtworkDownloader(
            urlopen=lambda url, timeout=None: (_ for _ in ()).throw(AssertionError("no network"))
        ),
    )
    entry = make_entry(tmp_path, platform="switch")
    assert service.ensure_cover_for_title(
        entry, platform="switch", title="Anything", identity_key="switch:X", library_root=library
    ).source == SOURCE_PLACEHOLDER
    assert service.ensure_cover_for_title(
        entry, platform="psp", title="", identity_key="psp:X", library_root=library
    ).source == SOURCE_PLACEHOLDER
    assert service.ensure_cover_for_title(
        None, platform="psp", title="Anything", identity_key="psp:X", library_root=library
    ).source == SOURCE_PLACEHOLDER


def test_ensure_cover_for_title_uses_named_boxarts_listing_after_404(tmp_path: Path):
    """When every generated candidate 404s, the Named_Boxarts directory listing
    gives the real file name; a unique match is downloaded."""
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, platform="3ds", name="Kirby Super Star Ultra")
    listing = (
        '<html><body><a href="Kirby%20-%20Super%20Star%20Ultra%20(USA).png">'
        "Kirby - Super Star Ultra (USA).png</a></body></html>"
    )
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        if url.endswith("/Named_Boxarts/"):
            return FakeResponse(listing.encode("utf-8"))
        if url.endswith("Kirby%20-%20Super%20Star%20Ultra%20(USA).png"):
            return FakeResponse(png_bytes())
        return FakeResponse(b"missing", status=404)

    service = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener))
    resolution = service.ensure_cover_for_title(
        entry,
        platform="3ds",
        title="Kirby Super Star Ultra",
        identity_key="3ds:name:kirby",
        library_root=library,
    )
    assert resolution.source == SOURCE_DOWNLOADED
    assert any(url.endswith("/Named_Boxarts/") for url in calls)
    boxart = [url for url in calls if "Named_Boxarts" in url and url.endswith(".png")]
    assert boxart and boxart[-1].endswith(
        "Kirby%20-%20Super%20Star%20Ultra%20(USA).png"
    )
    assert "Nintendo%20-%20Nintendo%203DS" in boxart[-1]
    assert cache.lookup("3ds", "3ds:name:kirby") is not None


def test_ensure_cover_for_title_multi_region_listing_prefers_usa(tmp_path: Path):
    """Several region variants of the same base title are now resolvable: the
    listing fallback picks the USA release instead of refusing to guess."""
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, platform="3ds", name="Mario Kart 7")
    listing = (
        '<a href="Mario%20Kart%207%20(E)%20(En,Fr,De).png">'
        "Mario Kart 7 (E) (En,Fr,De).png</a>"
        '<a href="Mario%20Kart%207%20(U)%20(En).png">'
        "Mario Kart 7 (U) (En).png</a>"
    )
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        if url.endswith("/Named_Boxarts/"):
            return FakeResponse(listing.encode("utf-8"))
        if url.endswith("Mario%20Kart%207%20(U)%20(En).png"):
            return FakeResponse(png_bytes())
        return FakeResponse(b"missing", status=404)

    service = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener))
    resolution = service.ensure_cover_for_title(
        entry,
        platform="3ds",
        title="Mario Kart 7",
        identity_key="3ds:name:mk7",
        library_root=library,
    )
    assert resolution.source == SOURCE_DOWNLOADED
    assert any(url.endswith("/Named_Boxarts/") for url in calls)
    boxart = [url for url in calls if "Named_Boxarts" in url and url.endswith(".png")]
    assert boxart and boxart[-1].endswith("Mario%20Kart%207%20(U)%20(En).png")
    assert cache.lookup("3ds", "3ds:name:mk7") is not None


def test_ensure_cover_for_title_conflicting_games_listing_is_not_downloaded(tmp_path: Path):
    """Two *different* games matching one query are a genuine conflict: the
    listing fallback must refuse to guess and leave the cover uncached."""
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, platform="3ds", name="Mario")
    listing = (
        '<a href="Mario%20Kart%207%20(USA).png">Mario Kart 7 (USA).png</a>'
        '<a href="Mario%20Party%20(USA).png">Mario Party (USA).png</a>'
    )
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        if url.endswith("/Named_Boxarts/"):
            return FakeResponse(listing.encode("utf-8"))
        return FakeResponse(b"missing", status=404)

    service = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener))
    resolution = service.ensure_cover_for_title(
        entry,
        platform="3ds",
        title="Mario",
        identity_key="3ds:name:mario",
        library_root=library,
    )
    assert resolution.source == SOURCE_PLACEHOLDER
    assert any(url.endswith("/Named_Boxarts/") for url in calls)
    assert cache.lookup("3ds", "3ds:name:mario") is None


def test_ensure_cover_for_title_ambiguous_listing_without_llm_stays_placeholder(
    tmp_path: Path,
):
    """Default OFF: an ambiguous listing behaves exactly as before -- no chooser
    is consulted, nothing is cached and the placeholder is returned."""
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, platform="3ds", name="Mario")
    listing = (
        '<a href="Mario%20Kart%207%20(USA).png">Mario Kart 7 (USA).png</a>'
        '<a href="Mario%20Party%20(USA).png">Mario Party (USA).png</a>'
    )
    calls = []
    offered = []

    def opener(url, timeout=None):
        calls.append(url)
        if url.endswith("/Named_Boxarts/"):
            return FakeResponse(listing.encode("utf-8"))
        return FakeResponse(b"missing", status=404)

    def chooser(candidates, query):
        offered.append((tuple(candidates), query))
        return "Mario Party (USA).png"

    service = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener))
    resolution = service.ensure_cover_for_title(
        entry,
        platform="3ds",
        title="Mario",
        identity_key="3ds:name:mario",
        library_root=library,
    )
    assert resolution.source == SOURCE_PLACEHOLDER
    assert offered == []
    assert cache.lookup("3ds", "3ds:name:mario") is None


def test_ensure_cover_for_title_llm_picks_ambiguous_candidate(tmp_path: Path):
    """When enabled and the model returns one of the offered file names, that
    cover is downloaded and cached."""
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, platform="3ds", name="Mario")
    listing = (
        '<a href="Mario%20Kart%207%20(USA).png">Mario Kart 7 (USA).png</a>'
        '<a href="Mario%20Party%20(USA).png">Mario Party (USA).png</a>'
    )
    calls = []
    offered = []

    def opener(url, timeout=None):
        calls.append(url)
        if url.endswith("/Named_Boxarts/"):
            return FakeResponse(listing.encode("utf-8"))
        if url.endswith("Mario%20Party%20(USA).png"):
            return FakeResponse(png_bytes())
        return FakeResponse(b"missing", status=404)

    def chooser(candidates, query):
        offered.append((tuple(candidates), query))
        return "Mario Party (USA).png"

    service = ArtworkService(
        cache=cache,
        downloader=ArtworkDownloader(urlopen=opener),
        llm_chooser=chooser,
    )
    resolution = service.ensure_cover_for_title(
        entry,
        platform="3ds",
        title="Mario",
        identity_key="3ds:name:mario",
        library_root=library,
    )
    assert resolution.source == SOURCE_DOWNLOADED
    assert offered == [
        (("Mario Kart 7 (USA).png", "Mario Party (USA).png"), "Mario")
    ]
    boxart = [url for url in calls if "Named_Boxarts" in url and url.endswith(".png")]
    assert boxart and boxart[-1].endswith("Mario%20Party%20(USA).png")
    assert cache.lookup("3ds", "3ds:name:mario") is not None


def test_ensure_cover_for_title_llm_nonsense_is_placeholder_and_uncached(
    tmp_path: Path,
):
    """A model that answers NONE, nonsense, or a name outside the list leaves the
    placeholder in place with no cache write."""
    library = tmp_path / "lib"
    entry = make_entry(tmp_path, platform="3ds", name="Mario")
    listing = (
        '<a href="Mario%20Kart%207%20(USA).png">Mario Kart 7 (USA).png</a>'
        '<a href="Mario%20Party%20(USA).png">Mario Party (USA).png</a>'
    )

    def opener(url, timeout=None):
        if url.endswith("/Named_Boxarts/"):
            return FakeResponse(listing.encode("utf-8"))
        return FakeResponse(b"missing", status=404)

    for answer in ("NONE", "I cannot tell", "Kirby Super Star Ultra (USA).png"):
        cache = CoverCache(library / ("covers-" + str(abs(hash(answer)))))
        service = ArtworkService(
            cache=cache,
            downloader=ArtworkDownloader(urlopen=opener),
            llm_chooser=lambda candidates, query, answer=answer: answer,
        )
        resolution = service.ensure_cover_for_title(
            entry,
            platform="3ds",
            title="Mario",
            identity_key="3ds:name:mario",
            library_root=library,
        )
        assert resolution.source == SOURCE_PLACEHOLDER
        assert cache.lookup("3ds", "3ds:name:mario") is None


def test_ensure_cover_for_title_llm_chooser_exception_keeps_placeholder(
    tmp_path: Path,
):
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, platform="3ds", name="Mario")
    listing = (
        '<a href="Mario%20Kart%207%20(USA).png">Mario Kart 7 (USA).png</a>'
        '<a href="Mario%20Party%20(USA).png">Mario Party (USA).png</a>'
    )

    def opener(url, timeout=None):
        if url.endswith("/Named_Boxarts/"):
            return FakeResponse(listing.encode("utf-8"))
        return FakeResponse(b"missing", status=404)

    def boom(candidates, query):
        raise RuntimeError("chooser exploded")

    service = ArtworkService(
        cache=cache,
        downloader=ArtworkDownloader(urlopen=opener),
        llm_chooser=boom,
    )
    resolution = service.ensure_cover_for_title(
        entry,
        platform="3ds",
        title="Mario",
        identity_key="3ds:name:mario",
        library_root=library,
    )
    assert resolution.source == SOURCE_PLACEHOLDER
    assert cache.lookup("3ds", "3ds:name:mario") is None


def test_ensure_cover_for_title_multi_region_never_calls_llm(tmp_path: Path):
    """Several region variants of one game are already resolved to USA by the
    deterministic matcher, so the optional LLM must not be consulted."""
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, platform="3ds", name="Mario Kart 7")
    listing = (
        '<a href="Mario%20Kart%207%20(Japan).png">Mario Kart 7 (Japan).png</a>'
        '<a href="Mario%20Kart%207%20(Europe).png">Mario Kart 7 (Europe).png</a>'
        '<a href="Mario%20Kart%207%20(USA).png">Mario Kart 7 (USA).png</a>'
    )

    def opener(url, timeout=None):
        if url.endswith("/Named_Boxarts/"):
            return FakeResponse(listing.encode("utf-8"))
        if url.endswith("Mario%20Kart%207%20(USA).png"):
            return FakeResponse(png_bytes())
        return FakeResponse(b"missing", status=404)

    def chooser(candidates, query):
        raise AssertionError("LLM must not be consulted for region variants")

    service = ArtworkService(
        cache=cache,
        downloader=ArtworkDownloader(urlopen=opener),
        llm_chooser=chooser,
    )
    resolution = service.ensure_cover_for_title(
        entry,
        platform="3ds",
        title="Mario Kart 7",
        identity_key="3ds:name:mk7",
        library_root=library,
    )
    assert resolution.source == SOURCE_DOWNLOADED
    assert Path(resolution.path).name == cache.identity_hash("3ds:name:mk7") + ".png"


def test_ensure_cover_for_title_ds_cartridge_uses_nds_boxart(tmp_path: Path):
    """A 3DS Checkpoint entry with no 3DS title id whose display name is a DS
    cartridge code plus title must resolve against the NDS system folder."""
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, platform="3ds", name="AZEJ Kirby Super Star Ultra")
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        if "Nintendo%20-%20Nintendo%20DS/Named_Boxarts/Kirby" in url:
            return FakeResponse(png_bytes())
        return FakeResponse(b"missing", status=404)

    service = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener))
    resolution = service.ensure_cover_for_title(
        entry,
        platform="3ds",
        title="AZEJ Kirby Super Star Ultra",
        identity_key="3ds:name:azej",
        library_root=library,
        title_id=None,
    )
    assert resolution.source == SOURCE_DOWNLOADED
    assert any("Nintendo%20-%20Nintendo%20DS" in url for url in calls)
    assert not any("Nintendo%20-%20Nintendo%203DS" in url for url in calls)
    # The NDS-routed cover is cached under the resolved platform, so a second
    # resolution is a zero-network hit.
    after_first = len(calls)
    again = service.ensure_cover_for_title(
        entry,
        platform="3ds",
        title="AZEJ Kirby Super Star Ultra",
        identity_key="3ds:name:azej",
        library_root=library,
        title_id=None,
    )
    assert again.source == SOURCE_DOWNLOADED
    assert again.path == resolution.path
    assert len(calls) == after_first


def test_ensure_cover_for_title_nano_assault_stays_on_3ds(tmp_path: Path):
    """``NANO Assault`` is a 3DS title whose first word merely looks like a DS
    code; it must be looked up under the 3DS system folder."""
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, platform="3ds", name="NANO Assault")
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        if "Nintendo%20-%20Nintendo%203DS/Named_Boxarts/NANO%20Assault" in url:
            return FakeResponse(png_bytes())
        return FakeResponse(b"missing", status=404)

    service = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener))
    resolution = service.ensure_cover_for_title(
        entry,
        platform="3ds",
        title="NANO Assault",
        identity_key="3ds:name:nano",
        library_root=library,
        title_id=None,
    )
    assert resolution.source == SOURCE_DOWNLOADED
    assert any("Nintendo%20-%20Nintendo%203DS" in url for url in calls)
    assert not any("/Nintendo%20-%20Nintendo%20DS/" in url for url in calls)


def test_ensure_cover_for_title_real_ds_serial_uses_nds(tmp_path: Path):
    """``IPKJ`` is a real NDS serial (Pokemon HeartGold, Japan); it must route
    to the NDS system folder."""
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, platform="3ds", name="IPKJ POKEMON HG")
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        if "Nintendo%20-%20Nintendo%20DS/Named_Boxarts/POKEMON%20HG" in url:
            return FakeResponse(png_bytes())
        return FakeResponse(b"missing", status=404)

    service = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener))
    resolution = service.ensure_cover_for_title(
        entry,
        platform="3ds",
        title="IPKJ POKEMON HG",
        identity_key="3ds:name:ipkj",
        library_root=library,
        title_id=None,
    )
    assert resolution.source == SOURCE_DOWNLOADED
    assert any("Nintendo%20-%20Nintendo%20DS" in url for url in calls)
    assert not any("Nintendo%20-%20Nintendo%203DS" in url for url in calls)


def test_ensure_cover_for_title_with_3ds_title_id_never_uses_nds(tmp_path: Path):
    """A present 3DS title id keeps the lookup on the 3DS system folder even when
    the display name happens to look like a DS cartridge name."""
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = make_entry(tmp_path, platform="3ds", name="MARIO KART 7")
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        if "Nintendo%20-%20Nintendo%203DS/Named_Boxarts/Mario%20Kart%207.png" in url:
            return FakeResponse(png_bytes())
        return FakeResponse(b"missing", status=404)

    service = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener))
    resolution = service.ensure_cover_for_title(
        entry,
        platform="3ds",
        title="MARIO KART 7",
        identity_key="3ds:0x00306",
        library_root=library,
        title_id="0x00306",
    )
    assert resolution.source == SOURCE_DOWNLOADED
    assert any("Nintendo%20-%20Nintendo%203DS" in url for url in calls)
    assert not any("/Nintendo%20-%20Nintendo%20DS/" in url for url in calls)


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
    assert service.ref_for("switch", "Anything") is None
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

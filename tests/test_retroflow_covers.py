"""Unit tests for RetroFlow / HexFlow physical retail box art provider."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from vajsave.artwork.cache import COVER_CACHE_DIR, CoverCache
from vajsave.artwork.downloader import ArtworkDownloader
from vajsave.artwork.retroflow_covers import (
    RetroFlowCoverProvider,
    clean_playstation_title_id,
)
from vajsave.artwork.service import (
    SOURCE_DOWNLOADED,
    ArtworkService,
)


def _make_png_bytes(size=(250, 320)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, (30, 60, 120))
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        return self.body

    def getheader(self, name: str, default: Any = None) -> Any:
        return default

    def close(self) -> None:
        pass


class DummyEntry:
    def __init__(self, platform: str, name: str, title_id: str = "", save_id: str = "") -> None:
        self.platform = platform
        self.name = name
        self.title_id = title_id
        self.save_id = save_id
        self.path = f"/{platform}/{name}"
        self.cover_path = None


def test_clean_playstation_title_id_canonical():
    assert clean_playstation_title_id("PCSD00071") == "PCSD00071"
    assert clean_playstation_title_id("pcsd00071") == "PCSD00071"
    assert clean_playstation_title_id("NPJH50107") == "NPJH50107"
    assert clean_playstation_title_id("ULJM05155") == "ULJM05155"
    assert clean_playstation_title_id("PCSE00507") == "PCSE00507"


def test_clean_playstation_title_id_folder_and_keys():
    assert clean_playstation_title_id("vita:PCSD00071") == "PCSD00071"
    assert clean_playstation_title_id("psp:NPJH50107") == "NPJH50107"
    assert clean_playstation_title_id("NPJH50107DATA00") == "NPJH50107"
    assert clean_playstation_title_id("ULJM051550000") == "ULJM05155"
    assert clean_playstation_title_id("NPJH50263USERID_0000") == "NPJH50263"
    assert clean_playstation_title_id("UCAS40063_GameData0") == "UCAS40063"


def test_clean_playstation_title_id_homebrew():
    assert clean_playstation_title_id("VITASHELL") == "VITASHELL"
    assert clean_playstation_title_id("SKGD3PL0Y") == "SKGD3PL0Y"
    assert clean_playstation_title_id("vitashell") == "VITASHELL"


def test_clean_playstation_title_id_rejects_regular_names():
    assert clean_playstation_title_id("LocoRoco") is None
    assert clean_playstation_title_id("Persona 4 Golden") is None
    assert clean_playstation_title_id("God of War") is None
    assert clean_playstation_title_id("") is None
    assert clean_playstation_title_id(None) is None


def test_retroflow_provider_supports():
    provider = RetroFlowCoverProvider()
    assert provider.supports("vita") is True
    assert provider.supports("PS Vita") is True
    assert provider.supports("psp") is True
    assert provider.supports("PSP") is True
    assert provider.supports("switch") is False
    assert provider.supports("3ds") is False
    assert provider.supports("gba") is False


def test_retroflow_provider_candidates():
    provider = RetroFlowCoverProvider()
    candidates_vita = provider.cover_candidates_for_title_id("vita", "PCSD00071")
    assert len(candidates_vita) >= 2
    assert "PSVita/PCSD00071.png" in candidates_vita[0].url
    assert candidates_vita[0].provider == "retroflow"
    assert candidates_vita[0].canonical_title == "PCSD00071"

    candidates_psp = provider.cover_candidates_for_title_id("psp", "NPJH50107DATA00")
    assert len(candidates_psp) >= 2
    assert "PSP/NPJH50107.png" in candidates_psp[0].url

    assert provider.cover_candidates_for_title_id("switch", "0100000000010000") == []
    assert provider.cover_candidates_for_title_id("vita", "NonTitleId") == []


def test_retroflow_provider_cover_for_and_find_cover():
    provider = RetroFlowCoverProvider()
    art = provider.cover_for("vita", "PCSD00071")
    assert art is not None
    assert "PCSD00071.png" in art.url

    assert provider.cover_for("vita", "Invalid Name") is None

    entry = DummyEntry("vita", "Killzone", title_id="PCSD00071")
    found = provider.find_cover(entry)
    assert found is not None
    assert "PCSD00071.png" in found.url


def test_ensure_cover_for_title_downloads_vita_retroflow(tmp_path: Path):
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = DummyEntry("vita", "KILLZONE: Mercenary", title_id="PCSD00071")
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        if "hexflow-covers" in url and "PCSD00071.png" in url:
            return FakeResponse(_make_png_bytes((250, 320)))
        return FakeResponse(b"missing", status=404)

    service = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener))
    res = service.ensure_cover_for_title(
        entry,
        platform="vita",
        title="KILLZONE: Mercenary",
        title_id="PCSD00071",
        identity_key="vita:PCSD00071",
        library_root=library,
    )
    assert res.source == SOURCE_DOWNLOADED
    assert res.path is not None
    assert any("hexflow-covers" in c and "PCSD00071.png" in c for c in calls)
    # Check cache manifest
    manifest = cache.manifest()
    assert "vita:PCSD00071" in manifest
    assert manifest["vita:PCSD00071"]["provider"] == "retroflow"


def test_ensure_cover_for_title_downloads_psp_retroflow_fallback(tmp_path: Path):
    library = tmp_path / "lib"
    cache = CoverCache(library / COVER_CACHE_DIR)
    entry = DummyEntry("psp", "機動戦士ガンダム ガンダムVS.ガンダムNEXT PLUS", title_id="NPJH50107")
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        # Libretro candidates return 404
        if "thumbnails.libretro.com" in url:
            return FakeResponse(b"404 not found", status=404)
        # RetroFlow returns retail box art
        if "hexflow-covers" in url and "NPJH50107.png" in url:
            return FakeResponse(_make_png_bytes((180, 320)))
        return FakeResponse(b"missing", status=404)

    service = ArtworkService(cache=cache, downloader=ArtworkDownloader(urlopen=opener))
    res = service.ensure_cover_for_title(
        entry,
        platform="psp",
        title="機動戦士ガンダム ガンダムVS.ガンダムNEXT PLUS",
        title_id="NPJH50107",
        identity_key="psp:NPJH50107",
        library_root=library,
    )
    assert res.source == SOURCE_DOWNLOADED
    assert res.path is not None
    # Verified: queried Libretro candidates first, then fetched from RetroFlow
    assert any("thumbnails.libretro.com" in c for c in calls)
    assert any("hexflow-covers" in c and "NPJH50107.png" in c for c in calls)
    manifest = cache.manifest()
    assert "psp:NPJH50107" in manifest
    assert manifest["psp:NPJH50107"]["provider"] == "retroflow"

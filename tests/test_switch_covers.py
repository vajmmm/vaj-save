from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from vajsave.artwork.cache import CoverCache
from vajsave.artwork.downloader import ArtworkDownloader
from vajsave.artwork.service import (
    ArtworkService,
    SOURCE_DOWNLOADED,
    SOURCE_PLACEHOLDER,
)
from vajsave.artwork.switch_covers import (
    GameTDBSwitchProvider,
    NlibSwitchProvider,
    get_gametdb_id_for_title_id,
    normalize_switch_title,
    switch_title_candidates,
)
from vajsave.models import SaveEntry


def _make_dummy_image(width: int = 352, height: int = 570) -> bytes:
    img = Image.new("RGB", (width, height), (100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class FakeDownloader(ArtworkDownloader):
    def __init__(self, routes: dict[str, bytes | None] | None = None) -> None:
        super().__init__()
        self.routes = routes or {}
        self.requested: list[str] = []

    def fetch(self, url: str) -> bytes | None:
        self.requested.append(url)
        return self.routes.get(url)


def test_normalize_switch_title() -> None:
    assert normalize_switch_title("Super Mario Odyssey") == "supermarioodyssey"
    assert normalize_switch_title("The Legend of Zelda: Breath of the Wild") == "thelegendofzeldabreathofthewild"
    assert normalize_switch_title("Pokémon Sword") == "pokemonsword"
    assert normalize_switch_title("1-2-Switch!") == "12switch"
    assert normalize_switch_title("") == ""


def test_switch_title_candidates() -> None:
    cands = switch_title_candidates("Super Mario Odyssey [Starter Pack]")
    assert "Super Mario Odyssey [Starter Pack]" in cands
    assert "Super Mario Odyssey" in cands

    cands_parens = switch_title_candidates("Bayonetta (USA)")
    assert "Bayonetta (USA)" in cands_parens
    assert "Bayonetta" in cands_parens

    # Title ID alone should be ignored as a title candidate
    cands_hex = switch_title_candidates("0100000000010000")
    assert len(cands_hex) == 0


def test_gametdb_provider_catalog_and_lookup() -> None:
    provider = GameTDBSwitchProvider()
    assert provider.supports("switch") is True
    assert provider.supports("gba") is False
    assert provider.supports("3ds") is False

    assert len(provider.catalog) > 1000

    match = provider.lookup_game("Super Mario Odyssey")
    assert match is not None
    gid, reg = match
    assert len(gid) in (4, 5)
    assert reg in ("US", "EN", "JA", "ZH")

    art = provider.cover_for("switch", "Super Mario Odyssey")
    assert art is not None
    assert art.provider == "gametdb"
    assert art.platform == "switch"
    assert "art.gametdb.com/switch/coverM" in art.url

    # Non-existent title
    assert provider.cover_for("switch", "NonExistentGameX999") is None
    assert provider.cover_for("gba", "Super Mario Odyssey") is None


def test_gametdb_cover_candidates() -> None:
    provider = GameTDBSwitchProvider()
    cands = provider.cover_candidates("Super Mario Odyssey")
    assert len(cands) >= 1
    assert any("coverM" in c.url for c in cands)


def test_nlib_provider() -> None:
    provider = NlibSwitchProvider()
    assert provider.supports("switch") is True
    assert provider.supports("nds") is False

    art = provider.cover_for_title_id("0100000000010000")
    assert art is not None
    assert art.url == "https://api.nlib.cc/nx/0100000000010000/icon"
    assert art.provider == "nlib"

    # Invalid title ID
    assert provider.cover_for_title_id("invalid_id") is None
    assert provider.cover_for("switch", "Super Mario Odyssey") is None
    assert provider.cover_for("switch", "0100000000010000") is not None


def test_ensure_cover_for_switch_gametdb(tmp_path: Path) -> None:
    cache = CoverCache(tmp_path / "covers")
    dummy_img = _make_dummy_image()
    downloader = FakeDownloader({
        "https://art.gametdb.com/switch/coverM/US/AAAC3.jpg": dummy_img,
        "https://art.gametdb.com/switch/coverM/EN/AAACA.jpg": dummy_img,
    })
    service = ArtworkService(cache=cache, downloader=downloader)

    entry = SaveEntry(
        platform="switch",
        source_id="switch_dbi",
        title_id="0100000000010000",
        display_name="Super Mario Odyssey",
        path=str(tmp_path / "save"),
    )

    res = service.ensure_cover_for_title(
        entry,
        platform="switch",
        title="Super Mario Odyssey",
        identity_key="switch:0100000000010000",
        library_root=tmp_path,
        title_id="0100000000010000",
    )

    assert res.source == SOURCE_DOWNLOADED
    assert res.path is not None
    assert Path(res.path).is_file()

    # Cached hit on second call performs zero network I/O
    downloader.requested.clear()
    res2 = service.ensure_cover_for_title(
        entry,
        platform="switch",
        title="Super Mario Odyssey",
        identity_key="switch:0100000000010000",
        library_root=tmp_path,
        title_id="0100000000010000",
    )
    assert res2.source == SOURCE_DOWNLOADED
    assert len(downloader.requested) == 0


def test_ensure_cover_for_switch_nlib_fallback(tmp_path: Path) -> None:
    cache = CoverCache(tmp_path / "covers")
    dummy_icon = _make_dummy_image(256, 256)
    downloader = FakeDownloader({
        "https://api.nlib.cc/nx/0100B02019488000/icon": dummy_icon,
    })
    service = ArtworkService(cache=cache, downloader=downloader)

    # An imaginary game not in GameTDB
    entry = SaveEntry(
        platform="switch",
        source_id="switch_jksv",
        title_id="0100B02019488000",
        display_name="Unknown Digital Indie",
        path=str(tmp_path / "save"),
    )

    res = service.ensure_cover_for_title(
        entry,
        platform="switch",
        title="Unknown Digital Indie",
        identity_key="switch:0100B02019488000",
        library_root=tmp_path,
        title_id="0100B02019488000",
    )

    assert res.source == SOURCE_DOWNLOADED
    assert res.path is not None
    assert Path(res.path).is_file()


def test_ensure_cover_for_switch_offline_fails_gracefully(tmp_path: Path) -> None:
    cache = CoverCache(tmp_path / "covers")
    downloader = FakeDownloader({})  # all downloads return None
    service = ArtworkService(cache=cache, downloader=downloader)

    entry = SaveEntry(
        platform="switch",
        source_id="switch_checkpoint",
        title_id="0100000000010000",
        display_name="Super Mario Odyssey",
        path=str(tmp_path / "save"),
    )

    res = service.ensure_cover_for_title(
        entry,
        platform="switch",
        title="Super Mario Odyssey",
        identity_key="switch:0100000000010000",
        library_root=tmp_path,
        title_id="0100000000010000",
    )

    assert res.source == SOURCE_PLACEHOLDER
    assert res.path is None


def test_get_switch_title_for_id() -> None:
    from vajsave.artwork.switch_covers import get_switch_title_for_id

    assert get_switch_title_for_id("010049900F556000") == "Super Mario 3D All-Stars"
    assert get_switch_title_for_id("01006BB00C6F0000") == "The Legend of Zelda: Link's Awakening"
    assert get_switch_title_for_id("01006A800016E000") == "Super Smash Bros. Ultimate"
    assert get_switch_title_for_id("0100000000010000") == "Super Mario Odyssey"
    # Case insensitivity and 0x prefix
    assert get_switch_title_for_id("0x01006a800016e000") == "Super Smash Bros. Ultimate"
    assert get_switch_title_for_id("invalid") is None


def test_parse_checkpoint_folder_name_switch() -> None:
    from vajsave.platforms.switch import _parse_checkpoint_folder_name

    # Pure hex folder (JKSV style)
    tid, name = _parse_checkpoint_folder_name("01006BB00C6F0000")
    assert tid == "01006BB00C6F0000"
    assert name == "The Legend of Zelda: Link's Awakening"

    # Duplicate hex tokens (Checkpoint style without title)
    tid, name = _parse_checkpoint_folder_name("0x01006A800016E000 0x01006A800016E000")
    assert tid == "01006A800016E000"
    assert name == "Super Smash Bros. Ultimate"

    # Hex + clean title
    tid, name = _parse_checkpoint_folder_name("0100000000010000 Super Mario Odyssey")
    assert tid == "0100000000010000"
    assert name == "Super Mario Odyssey"

    # Name only (DBI style)
    tid, name = _parse_checkpoint_folder_name("Mario Kart 8")
    assert tid is None
    assert name == "Mario Kart 8"


def test_ensure_cover_for_switch_pure_hex_folder(tmp_path: Path) -> None:
    cache = CoverCache(tmp_path / "covers")
    dummy_img = _make_dummy_image()
    # GameTDB retail cover for Super Smash Bros. Ultimate is AAABA
    downloader = FakeDownloader({
        "https://art.gametdb.com/switch/coverM/EN/AAABA.jpg": dummy_img,
    })
    service = ArtworkService(cache=cache, downloader=downloader)

    # Save entry with only hex title ID as display_name
    entry = SaveEntry(
        platform="switch",
        source_id="switch_jksv",
        title_id="01006A800016E000",
        display_name="01006A800016E000",
        path=str(tmp_path / "save"),
    )

    res = service.ensure_cover_for_title(
        entry,
        platform="switch",
        title="01006A800016E000",
        identity_key="switch:01006A800016E000",
        library_root=tmp_path,
        title_id="01006A800016E000",
    )

    assert res.source == SOURCE_DOWNLOADED
    assert res.path is not None
    assert Path(res.path).is_file()


def test_get_switch_id_for_title():
    from vajsave.artwork.switch_covers import get_switch_id_for_title, get_switch_title_for_id

    # Canonical known titles
    assert get_switch_id_for_title("Super Smash Bros. Ultimate") == "01006A800016E000"
    assert get_switch_id_for_title("Super Mario 3D All-Stars") == "010049900F556000"
    assert get_switch_id_for_title("The Legend of Zelda: Link's Awakening") == "01006BB00C6F0000"

    # Catalog titles with punctuation/trademarks
    assert get_switch_id_for_title("DARK SOULS REMASTERED") == "01004AB00A260000"
    assert get_switch_id_for_title("Bayonetta 3") == "01004A4010FEA000"
    assert get_switch_id_for_title("Donkey Kong Country Tropical Freeze") == "0100C1F0051B6000"
    assert get_switch_id_for_title("DRAGON QUEST BUILDERS 2") == "010042000A986000"
    assert get_switch_id_for_title("Kirby and the Forgotten Land") == "010000500D0FC000"

    # Prefix matches & aliases
    assert get_switch_id_for_title("Brotato") == "01002EF01A316000"
    assert get_switch_id_for_title("유니콘 오버로드") == "010069401ADB8000"
    assert get_switch_id_for_title("페르소나 5 더 로열") == "01005CA01580E000"
    assert get_switch_id_for_title("甜蜜夏日 ～Perfect Edition～") == "01004E500DB9E000"
    assert get_switch_id_for_title("秽翼的尤斯蒂娅") == "01003C7017BB4000"

    # Canonical name resolution
    assert get_switch_title_for_id("01006BB00C6F0000") == "The Legend of Zelda: Link's Awakening"


def test_switch_identity_reverse_resolution(tmp_path: Path):
    from vajsave.identity.switch import resolve

    # DBI save entry without title_id
    entry = SaveEntry(
        platform="switch",
        source_id="switch_dbi",
        title_id="",
        display_name="Kirby and the Forgotten Land",
        path=str(tmp_path / "Installed games" / "Kirby and the Forgotten Land" / "user"),
    )

    res = resolve(entry, None)
    assert res.is_resolved is True
    assert res.identity is not None
    assert res.identity.title_id == "010000500D0FC000"
    assert res.identity.title == "Kirby and the Forgotten Land"


def test_get_gametdb_id_for_title_id() -> None:
    # Direct Title ID mapping to GameTDB (id, region)
    assert get_gametdb_id_for_title_id("01006BB00C6F0000") == ("AR3NA", "EN")
    assert get_gametdb_id_for_title_id("01004AB00A260000") == ("AK63B", "US")
    assert get_gametdb_id_for_title_id("0100C1F0051B6000") == ("AFWTA", "EN")
    assert get_gametdb_id_for_title_id("01006A800016E000") == ("AAABA", "EN")
    assert get_gametdb_id_for_title_id("0100E26011DBE000") == ("AQMXA", "EN")
    # Case insensitivity and 0x prefix
    assert get_gametdb_id_for_title_id("0x01006bb00c6f0000") == ("AR3NA", "EN")
    # Invalid ID
    assert get_gametdb_id_for_title_id("invalid") is None
    assert get_gametdb_id_for_title_id(None) is None


def test_gametdb_provider_title_id_methods() -> None:
    provider = GameTDBSwitchProvider()
    assert provider.lookup_by_title_id("01006BB00C6F0000") == ("AR3NA", "EN")

    art = provider.cover_for_title_id("01006BB00C6F0000")
    assert art is not None
    assert art.provider == "gametdb"
    assert art.filename == "AR3NA.jpg"
    assert "art.gametdb.com/switch/coverM/EN/AR3NA.jpg" in art.url

    cands = provider.cover_candidates_for_title_id("01006BB00C6F0000")
    assert len(cands) >= 1
    assert any("AR3NA" in c.url for c in cands)


def test_ensure_cover_for_switch_title_id_first(tmp_path: Path) -> None:
    cache = CoverCache(tmp_path / "covers")
    dummy_img = _make_dummy_image()
    # GameTDB retail cover for Zelda Link's Awakening is AR3NA
    downloader = FakeDownloader({
        "https://art.gametdb.com/switch/coverM/EN/AR3NA.jpg": dummy_img,
    })
    service = ArtworkService(cache=cache, downloader=downloader)

    # Save entry with Title ID and an uncataloged / non-English display name
    entry = SaveEntry(
        platform="switch",
        source_id="switch_checkpoint",
        title_id="01006BB00C6F0000",
        display_name="织梦岛 (自制中文存档)",
        path=str(tmp_path / "save"),
    )

    res = service.ensure_cover_for_title(
        entry,
        platform="switch",
        title="织梦岛 (自制中文存档)",
        identity_key="switch:01006BB00C6F0000",
        library_root=tmp_path,
        title_id="01006BB00C6F0000",
    )

    assert res.source == SOURCE_DOWNLOADED
    assert res.path is not None
    assert Path(res.path).is_file()
    # Verify that the direct GameTDB URL for AR3NA was requested first
    assert "https://art.gametdb.com/switch/coverM/EN/AR3NA.jpg" in downloader.requested


def test_ensure_cover_for_switch_nlib_when_gametdb_404(tmp_path: Path) -> None:
    cache = CoverCache(tmp_path / "covers")
    dummy_icon = _make_dummy_image(256, 256)
    downloader = FakeDownloader({
        # GameTDB returns None (simulating 404), Nlib succeeds
        "https://api.nlib.cc/nx/0100B02019488000/icon": dummy_icon,
    })
    service = ArtworkService(cache=cache, downloader=downloader)

    entry = SaveEntry(
        platform="switch",
        source_id="switch_jksv",
        title_id="0100B02019488000",
        display_name="Digital Exclusive Game",
        path=str(tmp_path / "save"),
    )

    res = service.ensure_cover_for_title(
        entry,
        platform="switch",
        title="Digital Exclusive Game",
        identity_key="switch:0100B02019488000",
        library_root=tmp_path,
        title_id="0100B02019488000",
    )

    assert res.source == SOURCE_DOWNLOADED
    assert res.path is not None
    assert Path(res.path).is_file()


def test_ensure_cover_upgrades_cached_nlib_to_gametdb(tmp_path: Path) -> None:
    cache = CoverCache(tmp_path / "covers")
    dummy_icon = _make_dummy_image(256, 256)
    dummy_retail = _make_dummy_image(352, 570)
    # Pre-populate cache with an nlib square icon
    stored = cache.store(
        "switch",
        "switch:01004AB00A260000",
        dummy_icon,
        provider="nlib",
        canonical_title="DARK SOULS",
        remote_url="https://api.nlib.cc/nx/01004AB00A260000/icon",
    )
    assert stored is not None
    assert cache.get_entry("switch:01004AB00A260000")["provider"] == "nlib"

    # Now provide GameTDB retail cover (AK63B)
    downloader = FakeDownloader({
        "https://art.gametdb.com/switch/coverM/US/AK63B.jpg": dummy_retail,
    })
    service = ArtworkService(cache=cache, downloader=downloader)
    entry = SaveEntry(
        platform="switch",
        source_id="switch_dbi",
        title_id="01004AB00A260000",
        display_name="Dark Souls Remastered",
        path=str(tmp_path / "save"),
    )

    res = service.ensure_cover_for_title(
        entry,
        platform="switch",
        title="Dark Souls Remastered",
        identity_key="switch:01004AB00A260000",
        library_root=tmp_path,
        title_id="01004AB00A260000",
    )

    assert res.source == SOURCE_DOWNLOADED
    # Manifest should now be upgraded to gametdb!
    entry_meta = cache.get_entry("switch:01004AB00A260000")
    assert entry_meta["provider"] == "gametdb"
    assert "AK63B" in entry_meta["remote_url"]




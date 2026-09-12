"""``AppState`` integration for the metadata and artwork layers."""

from __future__ import annotations

import zlib
import hashlib
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image

from vajsave.app_state import AppState
from vajsave.artwork import COVER_CACHE_DIR, ArtworkDownloader, ArtworkService, CoverCache
from vajsave.artwork.service import (
    SOURCE_DOWNLOADED,
    SOURCE_EMBEDDED,
    SOURCE_PLACEHOLDER,
    SOURCE_USER,
)
from vajsave.models import SaveEntry


def make_gba_rom(title: str = "APOTRIS", code: str = "APTR", size: int = 0x200) -> bytes:
    data = bytearray(size)
    data[0xA0:0xA0 + 12] = title.ljust(12)[:12].encode("ascii")
    data[0xAC:0xB0] = code.ljust(4)[:4].encode("ascii")
    return bytes(data)


def make_nds_rom(title: str = "MAPLESTORYDS", code: str = "YMPK", size: int = 0x200) -> bytes:
    data = bytearray(size)
    data[0x00:0x0C] = title.ljust(12)[:12].encode("ascii")
    data[0x0C:0x10] = code.ljust(4)[:4].encode("ascii")
    return bytes(data)


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (4, 4), (1, 2, 3, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _digest(blob: bytes):
    return hashlib.sha1(blob).hexdigest(), format(zlib.crc32(blob) & 0xFFFFFFFF, "08x")


def _dat(directory: Path, title: str, sha1: str, crc: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "gba.dat"
    path.write_text(
        f'<?xml version="1.0"?><datafile><header><name>Nintendo - Game Boy Advance</name></header>'
        f'<game name="{title}"><rom name="{title}.gba" crc="{crc}" sha1="{sha1}"/></game></datafile>',
        encoding="utf-8",
    )
    return path


def _entry(tmp_path: Path, *, name="Apotris", cover_path=None) -> SaveEntry:
    save = tmp_path / "SAVEGAME" / f"{name}.sav"
    save.parent.mkdir(parents=True, exist_ok=True)
    save.write_bytes(b"x")
    return SaveEntry(
        platform="gba",
        source_id="gba_test",
        display_name=name,
        path=str(save),
        cover_path=cover_path,
    )


def _entry_nds(tmp_path: Path, *, name="MapleStory DS", cover_path=None) -> SaveEntry:
    save = tmp_path / "SAVEGAME" / f"{name}.sav"
    save.parent.mkdir(parents=True, exist_ok=True)
    save.write_bytes(b"x")
    return SaveEntry(
        platform="nds",
        source_id="nds_test",
        display_name=name,
        path=str(save),
        cover_path=cover_path,
    )


class _FakeResponse:
    def __init__(self, data: bytes, status: int = 200) -> None:
        self._data = data
        self.status = status
        self.headers = {}

    def read(self, n=-1):
        if n is None or n < 0:
            return self._data
        return self._data[:n]

    def close(self):
        return None


def test_appstate_serial_fallback_caches_and_downloads_by_canonical_title(tmp_path: Path):
    """A patched/汉化 GBA ROM whose digest misses the index still gets metadata
    from its header game code, and the cover pipeline then downloads by the
    resolved canonical title under the real ROM identity key."""
    rom_dir = tmp_path / "roms"
    rom_dir.mkdir()
    rom = rom_dir / "Apotris (Chinese).gba"
    rom.write_bytes(make_gba_rom(title="APOTRIS", code="Z9ZQ"))

    dat_dir = tmp_path / "dats"
    dat_dir.mkdir()
    # The digest deliberately does NOT match the patched ROM; only the serial does.
    (dat_dir / "gba.dat").write_text(
        '<?xml version="1.0"?><datafile><header><name>Nintendo - Game Boy Advance</name>'
        '</header><game name="Apotris - Rhythm Game (USA)">'
        '<rom name="x.gba" crc="00000001" sha1="' + "a" * 40 + '" serial="Z9ZQ"/>'
        '</game></datafile>',
        encoding="utf-8",
    )

    state = AppState(library_root=tmp_path / "lib")
    state.set_rom_dirs(rom_dir, None)
    state.set_libretro_dir(dat_dir)
    entry = _entry(tmp_path)

    result = state.resolve_save_identity(entry)
    assert result.is_resolved
    metadata = state.resolve_save_metadata(entry, result.identity)
    assert metadata is not None
    assert metadata.canonical_title == "Apotris - Rhythm Game (USA)"
    assert metadata.identity_key == result.identity.identity_key
    assert state.cached_save_metadata(result.identity).canonical_title == (
        "Apotris - Rhythm Game (USA)"
    )

    urls = []

    def opener(url, timeout=None):
        urls.append(url)
        return _FakeResponse(png_bytes())

    state._artwork_service = ArtworkService(
        cache=CoverCache(state.library_root / COVER_CACHE_DIR),
        downloader=ArtworkDownloader(urlopen=opener),
    )
    cover = state.ensure_save_cover(entry, result, metadata)
    assert cover.source == SOURCE_DOWNLOADED
    assert urls and "Apotris%20-%20Rhythm%20Game%20(USA)" in urls[0]
    assert urls[0].startswith(
        "https://thumbnails.libretro.com/Nintendo%20-%20Game%20Boy%20Advance/Named_Boxarts/"
    )


def test_appstate_bundled_serial_fallback_resolves_gyakuten2_and_cover(tmp_path: Path):
    """The real 汉化逆转裁判2 shape: the digest misses the bundled index and the
    A3GJ game code is shared with a Gyakuten Saiban 3 demo, yet the single retail
    family resolves and the cover is fetched by its canonical title."""
    rom_dir = tmp_path / "roms"
    rom_dir.mkdir()
    rom = rom_dir / "Gyakuten Saiban 2 (Chinese).gba"
    rom.write_bytes(make_gba_rom(title="GYAKUTEN_SA2", code="A3GJ"))

    state = AppState(library_root=tmp_path / "lib")
    state.set_rom_dirs(rom_dir, None)
    # No explicit libretro dir: the bundled offline index is the provider.
    entry = _entry(tmp_path, name="Gyakuten Saiban 2")

    result = state.resolve_save_identity(entry)
    assert result.is_resolved
    assert result.identity.game_code == "A3GJ"
    metadata = state.resolve_save_metadata(entry, result.identity)
    assert metadata is not None
    assert metadata.canonical_title == "Gyakuten Saiban 2 (Japan)"

    urls = []

    def opener(url, timeout=None):
        urls.append(url)
        return _FakeResponse(png_bytes())

    state._artwork_service = ArtworkService(
        cache=CoverCache(state.library_root / COVER_CACHE_DIR),
        downloader=ArtworkDownloader(urlopen=opener),
    )
    cover = state.ensure_save_cover(entry, result, metadata)
    assert cover.source == SOURCE_DOWNLOADED
    assert urls and "Gyakuten%20Saiban%202%20(Japan)" in urls[0]


def test_appstate_nds_serial_fallback_resolves_modified_rom(tmp_path: Path):
    """A patched/汉化 NDS ROM whose digest misses the index still gets metadata
    from its 4-char header game code and header title."""
    rom_dir = tmp_path / "roms"
    rom_dir.mkdir()
    rom = rom_dir / "MapleStory DS (Chinese).nds"
    rom.write_bytes(make_nds_rom(title="MAPLESTORYDS", code="YMPK"))

    dat_dir = tmp_path / "dats"
    dat_dir.mkdir()
    # The digest deliberately does NOT match the patched ROM; only the serial does.
    (dat_dir / "nds.dat").write_text(
        '<?xml version="1.0"?><datafile><header><name>Nintendo - Nintendo DS</name>'
        '</header><game name="MapleStory DS (Korea)">'
        '<rom name="x.nds" crc="00000001" sha1="' + "a" * 40 + '" serial="YMPK"/>'
        '</game></datafile>',
        encoding="utf-8",
    )

    state = AppState(library_root=tmp_path / "lib")
    state.set_rom_dirs(None, rom_dir)
    state.set_libretro_dir(dat_dir)
    entry = _entry_nds(tmp_path)

    result = state.resolve_save_identity(entry)
    assert result.is_resolved
    assert result.identity.game_code == "YMPK"
    metadata = state.resolve_save_metadata(entry, result.identity)
    assert metadata is not None
    assert metadata.canonical_title == "MapleStory DS (Korea)"
    assert metadata.identity_key == result.identity.identity_key


def test_appstate_nds_ambiguous_serial_without_title_stays_unresolved(tmp_path: Path):
    """An ambiguous NDS serial must stay unresolved when no distinctive header
    title is available, instead of guessing a wrong game."""
    rom_dir = tmp_path / "roms"
    rom_dir.mkdir()
    rom = rom_dir / "Unknown.nds"
    # Empty header title so the title stage cannot disambiguate.
    rom.write_bytes(make_nds_rom(title="", code="AZEJ"))

    dat_dir = tmp_path / "dats"
    dat_dir.mkdir()
    (dat_dir / "nds.dat").write_text(
        '<?xml version="1.0"?><datafile><header><name>Nintendo - Nintendo DS</name></header>'
        '<game name="Zelda no Densetsu - Mugen no Sunadokei (Japan)">'
        '<rom name="a.nds" crc="00000001" sha1="' + "a" * 40 + '" serial="AZEJ"/></game>'
        '<game name="Dragon Quest V - Tenkuu no Hanayome (Japan)">'
        '<rom name="b.nds" crc="00000002" sha1="' + "b" * 40 + '" serial="AZEJ"/></game>'
        '</datafile>',
        encoding="utf-8",
    )

    state = AppState(library_root=tmp_path / "lib")
    state.set_rom_dirs(None, rom_dir)
    state.set_libretro_dir(dat_dir)
    entry = _entry_nds(tmp_path, name="Unknown")

    result = state.resolve_save_identity(entry)
    assert result.identity is not None
    assert state.resolve_save_metadata(entry, result.identity) is None


def test_appstate_resolves_metadata_from_configured_index(tmp_path: Path):
    rom_dir = tmp_path / "roms"
    rom_dir.mkdir()
    rom = rom_dir / "Apotris.gba"
    rom.write_bytes(make_gba_rom())
    sha1, crc = _digest(rom.read_bytes())
    dat_dir = _dat(tmp_path / "dats", "Apotris - Rhythm Game (USA)", sha1, crc)

    state = AppState(library_root=tmp_path / "lib")
    state.set_rom_dirs(rom_dir, None)
    state.set_libretro_dir(dat_dir)
    entry = _entry(tmp_path)

    assert state.cached_save_metadata(None) is None
    result = state.resolve_save_identity(entry)
    assert result.is_resolved
    assert state.cached_save_metadata(result.identity) is None  # not looked up yet
    metadata = state.resolve_save_metadata(entry, result.identity)
    assert metadata is not None and metadata.canonical_title == "Apotris - Rhythm Game (USA)"
    # Now the cache-only path sees it without touching the provider.
    assert state.cached_save_metadata(result.identity).canonical_title == "Apotris - Rhythm Game (USA)"


def test_appstate_metadata_none_for_unknown_rom(tmp_path: Path):
    state = AppState(library_root=tmp_path / "lib")
    state.set_libretro_dir(tmp_path / "empty_dats")
    entry = _entry(tmp_path)
    assert state.resolve_save_metadata(entry) is None


def test_appstate_metadata_unsupported_platform_is_none(tmp_path: Path):
    from vajsave.identity.models import GameIdentity

    state = AppState(library_root=tmp_path / "lib")
    identity = GameIdentity(identity_key="psp:ULJM05800", platform="psp", title="Game")
    assert state.metadata_resolver.resolve(identity) is None
    assert state.cached_save_metadata(identity) is None


def test_appstate_cover_fallback_order(tmp_path: Path):
    library = tmp_path / "lib"
    state = AppState(library_root=library)
    entry = _entry(tmp_path)

    assert state.resolve_save_cover(entry).source == SOURCE_PLACEHOLDER

    embedded = tmp_path / "icon.png"
    embedded.write_bytes(png_bytes())
    entry.cover_path = str(embedded)
    assert state.resolve_save_cover(entry).source == SOURCE_EMBEDDED

    user_dir = library / "covers" / "gba"
    user_dir.mkdir(parents=True)
    (user_dir / "Apotris.png").write_bytes(png_bytes())
    assert state.resolve_save_cover(entry).source == SOURCE_USER


def test_appstate_ensure_cover_without_metadata_is_local_only(tmp_path: Path):
    state = AppState(library_root=tmp_path / "lib")
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        raise AssertionError("no network without metadata")

    state._artwork_service = ArtworkService(
        cache=CoverCache(tmp_path / "lib" / COVER_CACHE_DIR),
        downloader=ArtworkDownloader(urlopen=opener),
    )
    entry = _entry(tmp_path)
    resolution = state.ensure_save_cover(entry, metadata=None)
    assert resolution.source == SOURCE_PLACEHOLDER
    assert calls == []


def _psp_entry(tmp_path: Path, *, with_icon: bool, title_id: str = "ULJM05800") -> SaveEntry:
    save_dir = tmp_path / "PSP" / "SAVEDATA" / title_id
    save_dir.mkdir(parents=True, exist_ok=True)
    (save_dir / "DATA.BIN").write_bytes(b"save")
    if with_icon:
        (save_dir / "ICON0.PNG").write_bytes(png_bytes())
    return SaveEntry(
        platform="psp",
        source_id="psp_test",
        display_name=title_id,
        path=str(save_dir),
        title_id=title_id,
    )


def _vita_entry(tmp_path: Path, *, title_id: str = "PCSE00120") -> SaveEntry:
    save_dir = tmp_path / "VITA" / title_id
    (save_dir / "sce_sys").mkdir(parents=True, exist_ok=True)
    (save_dir / "data.bin").write_bytes(b"save")
    return SaveEntry(
        platform="vita",
        source_id="vita_test",
        display_name=title_id,
        path=str(save_dir),
        title_id=title_id,
    )


def test_appstate_psp_cover_uses_sfo_title_without_icon(tmp_path: Path, psp_sfo_bytes: bytes):
    """A PSP save with no embedded ICON0 falls back to the PARAM.SFO title and
    downloads the libretro boxart under ``Sony - PlayStation Portable``."""
    entry = _psp_entry(tmp_path, with_icon=False)
    (Path(entry.path) / "PARAM.SFO").write_bytes(psp_sfo_bytes)

    state = AppState(library_root=tmp_path / "lib")
    result = state.resolve_save_identity(entry)
    assert result.is_resolved
    assert result.identity.title == "Monster Hunter Portable 3rd"

    urls = []

    def opener(url, timeout=None):
        urls.append(url)
        return _FakeResponse(png_bytes())

    state._artwork_service = ArtworkService(
        cache=CoverCache(state.library_root / COVER_CACHE_DIR),
        downloader=ArtworkDownloader(urlopen=opener),
    )
    cover = state.ensure_save_cover(entry, result)
    assert cover.source == SOURCE_DOWNLOADED
    assert urls and "Sony%20-%20PlayStation%20Portable" in urls[0]
    assert "Monster%20Hunter%20Portable%203rd" in urls[0]


def test_appstate_psp_icon0_skips_network(tmp_path: Path, psp_sfo_bytes: bytes):
    """When the save folder already ships an ICON0.PNG, the PSP path must not
    go online:"""
    entry = _psp_entry(tmp_path, with_icon=True)
    (Path(entry.path) / "PARAM.SFO").write_bytes(psp_sfo_bytes)

    state = AppState(library_root=tmp_path / "lib")
    result = state.resolve_save_identity(entry)
    calls = []

    state._artwork_service = ArtworkService(
        cache=CoverCache(state.library_root / COVER_CACHE_DIR),
        downloader=ArtworkDownloader(
            urlopen=lambda url, timeout=None: calls.append(url) or _FakeResponse(png_bytes())
        ),
    )
    cover = state.ensure_save_cover(entry, result)
    assert cover.source == SOURCE_EMBEDDED
    assert calls == []


def test_appstate_vita_cover_uses_sfo_title(tmp_path: Path, vita_sfo_bytes: bytes):
    entry = _vita_entry(tmp_path)
    (Path(entry.path) / "sce_sys" / "param.sfo").write_bytes(vita_sfo_bytes)

    state = AppState(library_root=tmp_path / "lib")
    result = state.resolve_save_identity(entry)
    assert result.is_resolved
    assert result.identity.title == "Persona 4 Golden"

    urls = []

    state._artwork_service = ArtworkService(
        cache=CoverCache(state.library_root / COVER_CACHE_DIR),
        downloader=ArtworkDownloader(
            urlopen=lambda url, timeout=None: urls.append(url) or _FakeResponse(png_bytes())
        ),
    )
    cover = state.ensure_save_cover(entry, result)
    assert cover.source == SOURCE_DOWNLOADED
    assert urls and "Sony%20-%20PlayStation%20Vita" in urls[0]
    assert "Persona%204%20Golden" in urls[0]


def test_appstate_gba_identity_without_canonical_metadata_never_network(tmp_path: Path):
    """GBA/NDS titles come from the canonical index only; without it the app
    must never guess a provider name (criterion: no network)."""
    rom_dir = tmp_path / "roms"
    rom_dir.mkdir()
    rom = rom_dir / "Apotris (Chinese).gba"
    rom.write_bytes(make_gba_rom(title="APOTRIS", code="Z9ZQ"))

    state = AppState(library_root=tmp_path / "lib")
    state.set_rom_dirs(rom_dir, None)
    entry = _entry(tmp_path)
    result = state.resolve_save_identity(entry)
    assert result.is_resolved

    calls = []
    state._artwork_service = ArtworkService(
        cache=CoverCache(state.library_root / COVER_CACHE_DIR),
        downloader=ArtworkDownloader(
            urlopen=lambda url, timeout=None: calls.append(url) or _FakeResponse(png_bytes())
        ),
    )
    cover = state.ensure_save_cover(entry, result, metadata=None)
    assert cover.source == SOURCE_PLACEHOLDER
    assert calls == []


def test_set_library_root_resets_metadata_and_artwork_services(tmp_path: Path):
    state = AppState(library_root=tmp_path / "lib")
    _ = state.metadata_service
    _ = state.artwork_service
    assert state._metadata_service is not None
    assert state._artwork_service is not None
    state.set_library_root(tmp_path / "other")
    assert state._metadata_service is None
    assert state._artwork_service is None


def test_set_libretro_dir_persists_and_clears(tmp_path: Path):
    state = AppState(library_root=tmp_path / "lib")
    dat_dir = tmp_path / "dats"
    dat_dir.mkdir()
    assert state.set_libretro_dir(dat_dir) == dat_dir
    from vajsave.library import load_app_config

    assert load_app_config()["libretro_dir"] == str(dat_dir)
    assert state.set_libretro_dir(None) is None
    assert "libretro_dir" not in load_app_config()


def test_settings_dialog_apply_libretro_dir(tmp_path: Path):
    from vajsave.app_ui import VajSaveApp

    state = AppState(library_root=tmp_path / "lib")

    class Stub:
        def __init__(self) -> None:
            self.state = state
            self.status = None

        def update_status(self, text: str) -> None:
            self.status = text

        def refresh_saves_ui(self) -> None:
            return

    stub = Stub()
    dat_dir = tmp_path / "dats"
    dat_dir.mkdir()
    VajSaveApp._apply_libretro_dir(stub, dat_dir)
    assert state.libretro_dir == dat_dir
    assert stub.status == "元数据目录已更新"
    VajSaveApp._apply_libretro_dir(stub, None)
    assert state.libretro_dir is None


def test_library_mode_shows_cover_cached_under_rom_identity_key(tmp_path: Path):
    """A backup persists the ROM identity_key, so browsing the local library hits
    the identity-hash cover cache instead of falling back to a placeholder."""
    from vajsave.library import game_key, load_catalog

    lib = tmp_path / "lib"
    save_dir = tmp_path / "PSP" / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "DATA.BIN").write_bytes(b"save-data")
    entry = SaveEntry(
        platform="psp",
        source_id="psp_test",
        display_name="PSP Game",
        path=str(save_dir),
        title_id="ULJM05800",
    )

    cache = CoverCache(lib / COVER_CACHE_DIR)
    stored = cache.store("psp", "psp:ULJM05800", png_bytes())
    assert stored is not None

    state = AppState(library_root=lib)
    assert state.import_save(entry) is not None
    assert load_catalog(lib).games[game_key(entry)].identity_key == "psp:ULJM05800"

    state.set_library_mode(True)
    row = state.visible_saves()[0]
    cover = state.resolve_save_cover(row)
    assert cover.source == SOURCE_DOWNLOADED
    assert Path(cover.path) == stored

    # The image stays in covers/; the snapshot tree only holds the save payload.
    snapshot_files = [p.name for p in Path(row.path).rglob("*") if p.is_file()]
    assert snapshot_files == ["DATA.BIN"]


def test_library_mode_without_identity_key_keeps_placeholder(tmp_path: Path):
    """A legacy record with no stored identity must not guess a cached cover."""
    from vajsave.library import backup_save

    lib = tmp_path / "lib"
    save_dir = tmp_path / "PSP" / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "DATA.BIN").write_bytes(b"save-data")
    entry = SaveEntry(
        platform="psp",
        source_id="psp_test",
        display_name="PSP Game",
        path=str(save_dir),
        title_id="ULJM05800",
    )
    # An older backup: no identity_key was recorded at backup time.
    backup_save(entry, lib, when=datetime(2024, 1, 1, 10, 0, 0))

    cache = CoverCache(lib / COVER_CACHE_DIR)
    assert cache.store("psp", "psp:ULJM05800", png_bytes()) is not None

    state = AppState(library_root=lib)
    state.set_library_mode(True)
    row = state.visible_saves()[0]
    assert not row.extra.get("identity_key")
    assert state.resolve_save_cover(row).source == SOURCE_PLACEHOLDER

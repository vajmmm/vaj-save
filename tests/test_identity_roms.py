"""GBA/NDS ROM matching tests: exact name, region tags, ambiguous, missing ROM,
deleted ROM, manual binding, moved save dir, and stable crc32/sha1 identity."""

import shutil
from pathlib import Path

from vajsave.identity import (
    BINDINGS_NAME,
    GameIdentity,
    GameIdentityResolver,
    SOURCE_MANUAL,
)
from vajsave.models import SaveEntry


def make_gba_rom(title="KIRBY GAME", code="KBRD", size=0x200) -> bytes:
    data = bytearray(size)
    data[0xA0:0xA0 + 12] = title.ljust(12)[:12].encode("ascii")
    data[0xAC:0xB0] = code.ljust(4)[:4].encode("ascii")
    return bytes(data)


def make_nds_rom(title="POKEMON", code="IPKE", size=0x200) -> bytes:
    data = bytearray(size)
    data[0:12] = title.ljust(12)[:12].encode("ascii")
    data[0x0C:0x10] = code.ljust(4)[:4].encode("ascii")
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


# --- GBA ---------------------------------------------------------------------


def test_gba_exact_name_match_resolves_from_rom(tmp_path: Path):
    rom_dir = _rom_dir(tmp_path)
    (rom_dir / "Kirby.gba").write_bytes(make_gba_rom())
    resolver = GameIdentityResolver(rom_dirs={"gba": [rom_dir]})

    result = resolver.resolve(entry("gba", "Kirby", tmp_path / "SAVER" / "Kirby.sav"))
    assert result.is_resolved
    assert result.identity.identity_key.startswith("gba:sha1:")
    assert result.identity.title == "KIRBY GAME"
    assert result.identity.game_code == "KBRD"
    assert result.identity.region is None
    assert result.identity.source == "rom"


def test_gba_region_tag_match(tmp_path: Path):
    rom_dir = _rom_dir(tmp_path)
    (rom_dir / "Pokemon Emerald (USA).gba").write_bytes(make_gba_rom())
    resolver = GameIdentityResolver(rom_dirs={"gba": [rom_dir]})

    result = resolver.resolve(entry("gba", "Pokemon Emerald", tmp_path / "Pokemon Emerald.sav"))
    assert result.is_resolved
    assert result.identity.region == "USA"


def test_gba_multiple_region_roms_are_ambiguous(tmp_path: Path):
    rom_dir = _rom_dir(tmp_path)
    (rom_dir / "Pokemon (USA).gba").write_bytes(make_gba_rom())
    (rom_dir / "Pokemon (Japan).gba").write_bytes(make_gba_rom(title="POKEMON JPN"))
    resolver = GameIdentityResolver(rom_dirs={"gba": [rom_dir]})

    result = resolver.resolve(entry("gba", "Pokemon", tmp_path / "Pokemon.sav"))
    assert result.status == "ambiguous"
    assert len(result.candidates) == 2
    assert {c.region for c in result.candidates} == {"USA", "Japan"}


def test_gba_missing_rom_is_unresolved(tmp_path: Path):
    resolver = GameIdentityResolver(rom_dirs={"gba": [_rom_dir(tmp_path)]})
    result = resolver.resolve(entry("gba", "Kirby", tmp_path / "Kirby.sav"))
    assert result.status == "unresolved"
    assert "ROM" in result.reason


def test_gba_deleted_rom_falls_back_to_binding(tmp_path: Path):
    rom_dir = _rom_dir(tmp_path)
    rom = rom_dir / "Kirby.gba"
    rom.write_bytes(make_gba_rom())
    binding_path = tmp_path / BINDINGS_NAME
    resolver = GameIdentityResolver(rom_dirs={"gba": [rom_dir]}, binding_path=binding_path)
    sav = entry("gba", "Kirby", tmp_path / "SAVER" / "Kirby.sav")

    first = resolver.resolve(sav)
    assert first.is_resolved

    rom.unlink()
    resolver.refresh()
    second = resolver.resolve(sav)
    assert second.is_resolved
    assert second.identity_key == first.identity_key
    assert second.identity.source == "binding"


def test_gba_manual_binding_without_rom(tmp_path: Path):
    resolver = GameIdentityResolver(binding_path=tmp_path / BINDINGS_NAME)
    sav = entry("gba", "Kirby", tmp_path / "Kirby.sav")
    stored = resolver.bind(
        sav,
        GameIdentity(identity_key="gba:sha1:cafe", platform="gba", title="Kirby", rom_sha1="cafe"),
    )
    assert stored.source == SOURCE_MANUAL

    result = resolver.resolve(sav)
    assert result.is_resolved
    assert result.identity_key == "gba:sha1:cafe"
    assert result.identity.source == SOURCE_MANUAL


def test_gba_binding_survives_moved_save_dir(tmp_path: Path):
    rom_dir = _rom_dir(tmp_path)
    (rom_dir / "Kirby.gba").write_bytes(make_gba_rom())
    binding_path = tmp_path / BINDINGS_NAME

    dir_a = tmp_path / "A"
    dir_a.mkdir()
    (dir_a / "Kirby.sav").write_bytes(b"save")
    resolver_a = GameIdentityResolver(rom_dirs={"gba": [rom_dir]}, binding_path=binding_path)
    first = resolver_a.resolve(entry("gba", "Kirby", dir_a / "Kirby.sav"))
    assert first.is_resolved

    dir_b = tmp_path / "B" / "moved"
    dir_b.mkdir(parents=True)
    shutil.move(str(dir_a / "Kirby.sav"), str(dir_b / "Kirby.sav"))

    # New resolver, no ROM dirs: only the persisted, path-independent binding can match.
    resolver_b = GameIdentityResolver(binding_path=binding_path)
    second = resolver_b.resolve(entry("gba", "Kirby", dir_b / "Kirby.sav"))
    assert second.is_resolved
    assert second.identity_key == first.identity_key
    assert second.identity.source == "binding"


def test_gba_sibling_rom_matches_without_config(tmp_path: Path):
    saver = tmp_path / "SAVER"
    saver.mkdir()
    (saver / "Kirby.sav").write_bytes(b"save")
    (saver / "Kirby.gba").write_bytes(make_gba_rom())
    resolver = GameIdentityResolver()
    result = resolver.resolve(entry("gba", "Kirby", saver / "Kirby.sav"))
    assert result.is_resolved


def test_gba_stable_crc32_and_sha1(tmp_path: Path):
    rom_dir = _rom_dir(tmp_path)
    (rom_dir / "Kirby.gba").write_bytes(make_gba_rom())
    sav = entry("gba", "Kirby", tmp_path / "Kirby.sav")

    first = GameIdentityResolver(rom_dirs={"gba": [rom_dir]}, binding_path=tmp_path / "b.json").resolve(sav)
    second = GameIdentityResolver(rom_dirs={"gba": [rom_dir]}, binding_path=tmp_path / "c.json").resolve(sav)
    assert first.identity.rom_sha1 == second.identity.rom_sha1
    assert first.identity.rom_crc32 == second.identity.rom_crc32
    assert first.identity_key == second.identity_key
    assert first.identity_key == f"gba:sha1:{first.identity.rom_sha1}"


def test_gba_never_reads_save_bytes(tmp_path: Path, monkeypatch):
    rom_dir = _rom_dir(tmp_path)
    (rom_dir / "Kirby.gba").write_bytes(make_gba_rom())
    saver = tmp_path / "SAVER"
    saver.mkdir()
    save_file = saver / "Kirby.sav"
    save_file.write_bytes(b"this payload must never be parsed")

    opened = []
    real_open = Path.open

    def tracking_open(self, *args, **kwargs):
        opened.append(str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)
    resolver = GameIdentityResolver(rom_dirs={"gba": [rom_dir]})
    result = resolver.resolve(entry("gba", "Kirby", save_file))
    assert result.is_resolved
    assert str(save_file) not in opened
    assert all(not name.endswith(".sav") for name in opened)


# --- NDS ---------------------------------------------------------------------


def test_nds_sibling_rom_matches_next_to_saves_dir(tmp_path: Path):
    rom_dir = tmp_path / "roms" / "nds"
    rom_dir.mkdir(parents=True)
    (rom_dir / "Game.nds").write_bytes(make_nds_rom())
    saves = rom_dir / "saves"
    saves.mkdir()
    (saves / "Game.sav").write_bytes(b"save")

    resolver = GameIdentityResolver()
    result = resolver.resolve(entry("nds", "Game", saves / "Game.sav"))
    assert result.is_resolved
    assert result.identity.identity_key.startswith("nds:sha1:")
    assert result.identity.title == "POKEMON"
    assert result.identity.game_code == "IPKE"


def test_nds_region_tag_and_ambiguous(tmp_path: Path):
    rom_dir = _rom_dir(tmp_path)
    (rom_dir / "Mario Kart DS (Europe).nds").write_bytes(make_nds_rom())
    (rom_dir / "Mario Kart DS (USA).nds").write_bytes(make_nds_rom(title="MARIO KART DS"))
    resolver = GameIdentityResolver(rom_dirs={"nds": [rom_dir]})
    result = resolver.resolve(entry("nds", "Mario Kart DS", tmp_path / "Mario Kart DS.sav"))
    assert result.status == "ambiguous"
    assert len(result.candidates) == 2


def test_nds_missing_rom_is_unresolved(tmp_path: Path):
    resolver = GameIdentityResolver()
    result = resolver.resolve(entry("nds", "Game", tmp_path / "Game.sav"))
    assert result.status == "unresolved"


def test_missing_rom_dirs_are_harmless(tmp_path: Path):
    resolver = GameIdentityResolver(rom_dirs={"gba": [tmp_path / "does-not-exist"]})
    result = resolver.resolve(entry("gba", "Kirby", tmp_path / "Kirby.sav"))
    assert result.status == "unresolved"

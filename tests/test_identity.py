"""Tests for the independent GameIdentity layer: value objects, naming,
digests, bindings, resolver dispatch and AppState integration."""

from pathlib import Path

import pytest

from vajsave.app_state import AppState
from vajsave.identity import (
    BINDINGS_NAME,
    STATUS_AMBIGUOUS,
    STATUS_PARTIAL,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
    BindingStore,
    GameIdentity,
    GameIdentityResolver,
    RomIndex,
    ambiguous,
    crc32_file,
    digest_file,
    display_name_from_stem,
    extract_region,
    normalize_title,
    partial,
    resolved,
    save_hint,
    sha1_file,
    strip_extension,
    unresolved,
)
from vajsave.models import SaveEntry
from vajsave.library import load_app_config, save_app_config


def make_entry(platform="gba", name="Kirby", path="/tmp/x/Kirby.sav", **kwargs) -> SaveEntry:
    return SaveEntry(
        platform=platform,
        source_id=f"{platform}_test",
        display_name=name,
        path=str(path),
        **kwargs,
    )


def make_gba_rom(title="KIRBY GAME", code="KBRD", size=0x200) -> bytes:
    data = bytearray(size)
    data[0xA0:0xA0 + 12] = title.ljust(12)[:12].encode("ascii")
    data[0xAC:0xB0] = code.ljust(4)[:4].encode("ascii")
    return bytes(data)


# --- value objects -----------------------------------------------------------


def test_status_constants_are_distinct():
    assert len({STATUS_RESOLVED, STATUS_PARTIAL, STATUS_AMBIGUOUS, STATUS_UNRESOLVED}) == 4


def test_result_constructors():
    identity = GameIdentity(identity_key="gba:sha1:aa", platform="gba", title="Kirby")
    ok = resolved(identity, reason="matched", save_path="/s")
    assert ok.status == STATUS_RESOLVED
    assert ok.is_resolved
    assert ok.identity_key == "gba:sha1:aa"
    assert ok.reason == "matched"
    assert ok.save_path == "/s"

    part = partial(identity, reason="no id")
    assert part.status == STATUS_PARTIAL
    assert not part.is_resolved

    amb = ambiguous((identity, identity), reason="two")
    assert amb.status == STATUS_AMBIGUOUS
    assert len(amb.candidates) == 2
    assert amb.identity is None

    un = unresolved(reason="nope")
    assert un.status == STATUS_UNRESOLVED
    assert un.identity_key is None


def test_game_identity_roundtrip():
    identity = GameIdentity(
        identity_key="nds:sha1:deadbeef",
        platform="nds",
        title="Pokemon",
        title_id="IPKE",
        region="USA",
        rom_sha1="deadbeef",
        rom_crc32="12345678",
        rom_path="/roms/Pokemon.nds",
        game_code="IPKE",
        source="rom",
    )
    data = identity.to_dict()
    assert data["region"] == "USA"
    assert GameIdentity.from_dict(data) == identity


def test_result_to_dict_includes_candidates_and_reason():
    identity = GameIdentity(identity_key="gba:sha1:aa", platform="gba", title="A")
    payload = ambiguous((identity,), reason="why", save_path="/s").to_dict()
    assert payload["status"] == STATUS_AMBIGUOUS
    assert payload["candidates"][0]["identity_key"] == "gba:sha1:aa"
    assert payload["reason"] == "why"
    assert payload["save_path"] == "/s"


# --- naming ------------------------------------------------------------------


def test_normalize_title_strips_region_and_punctuation():
    assert normalize_title("Pokemon Emerald (USA) (Rev 1) [!].gba") == "pokemon emerald"
    assert normalize_title("Kirby_-_Nightmare.sav") == "kirby nightmare"
    assert normalize_title("") == ""


def test_extract_region_variants():
    assert extract_region("Game (USA).gba") == "USA"
    assert extract_region("Game (E).gba") == "Europe"
    assert extract_region("Game (Japan) (Rev 1).nds") == "Japan"
    assert extract_region("Game [Europe]") == "Europe"
    assert extract_region("Plain Game.gba") is None


def test_display_name_and_strip_extension():
    assert display_name_from_stem("Pokemon Emerald (USA).gba") == "Pokemon Emerald"
    assert display_name_from_stem("some_game.sav") == "some game"
    assert strip_extension("Kirby.sav") == "Kirby"
    assert strip_extension("Pokemon. Emerald") == "Pokemon. Emerald"
    assert strip_extension("noext") == "noext"


def test_save_hint_prefers_display_name_then_path():
    assert save_hint(make_entry(name="Kirby")) == "Kirby"
    assert save_hint(make_entry(name="", path="/a/b/Fire Red.sav")) == "Fire Red"
    assert save_hint(make_entry(name="", path="/a/b/Folder")) == "Folder"


# --- digests -----------------------------------------------------------------


def test_digest_file_is_stable_and_matches_helpers(tmp_path: Path):
    target = tmp_path / "rom.gba"
    target.write_bytes(b"\x01\x02\x03" * 1000)
    sha1, crc = digest_file(target)
    assert sha1 == sha1_file(target)
    assert crc == crc32_file(target)
    assert len(sha1) == 40
    assert len(crc) == 8
    # a different chunk size must not change the result
    assert digest_file(target, chunk_size=7) == (sha1, crc)


# --- bindings ----------------------------------------------------------------


def test_binding_key_is_path_independent(tmp_path: Path):
    a = make_entry(platform="gba", name="Kirby", path=str(tmp_path / "a" / "Kirby.sav"))
    b = make_entry(platform="gba", name="Kirby", path=str(tmp_path / "b" / "moved" / "Kirby.sav"))
    assert BindingStore.key_for(a) == BindingStore.key_for(b)
    assert BindingStore.key_for(a) == "gba:kirby"


def test_binding_store_kind_and_manual_precedence(tmp_path: Path):
    store = BindingStore(tmp_path / "bindings.json")
    entry = make_entry(name="Kirby")
    auto = GameIdentity(identity_key="gba:sha1:aa", platform="gba", title="Kirby")

    stored = store.set(entry, auto, manual=False)
    assert stored.source == "binding"
    loaded = store.get(entry)
    assert loaded is not None and loaded.source == "binding"
    assert loaded.rom_sha1 is None

    store.set(entry, auto, manual=True)
    assert store.get(entry).source == "manual"


def test_binding_store_persists_across_instances(tmp_path: Path):
    path = tmp_path / "bindings.json"
    entry = make_entry(name="Kirby")
    identity = GameIdentity(identity_key="gba:sha1:aa", platform="gba", title="Kirby", rom_sha1="aa")
    BindingStore(path).set(entry, identity, manual=True)

    assert path.is_file()
    reloaded = BindingStore(path)
    loaded = reloaded.get(entry)
    assert loaded is not None
    assert loaded.identity_key == "gba:sha1:aa"
    assert loaded.source == "manual"


def test_binding_store_remove_and_corrupt_file(tmp_path: Path):
    path = tmp_path / "bindings.json"
    entry = make_entry(name="Kirby")
    store = BindingStore(path)
    store.set(entry, GameIdentity(identity_key="gba:sha1:aa", platform="gba", title="K"))
    assert store.remove(entry) is True
    assert store.get(entry) is None

    path.write_text("{not json", encoding="utf-8")
    assert BindingStore(path).all() == {}


def test_binding_store_in_memory_has_no_file():
    store = BindingStore()
    entry = make_entry(name="Kirby")
    store.set(entry, GameIdentity(identity_key="gba:sha1:aa", platform="gba", title="K"))
    assert store.get(entry) is not None
    assert store.save() is False


# --- resolver dispatch -------------------------------------------------------


def test_resolver_unknown_platform_is_unresolved():
    resolver = GameIdentityResolver()
    result = resolver.resolve(make_entry(platform="dreamcast", name="Sonic"))
    assert result.status == STATUS_UNRESOLVED
    assert "不支持" in result.reason


def test_resolver_module_for_is_case_and_space_insensitive():
    resolver = GameIdentityResolver()
    assert resolver.module_for(" GBA ") is not None
    assert resolver.module_for("3ds") is not None
    assert resolver.module_for(None) is None


def test_resolve_many_isolates_per_save_failures(monkeypatch):
    import vajsave.identity.gba as gba_module

    def boom(entry, ctx):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(gba_module, "resolve", boom)
    resolver = GameIdentityResolver()
    good = make_entry(platform="psp", name="MH", title_id="ULJM05800")
    bad = make_entry(platform="gba", name="Kirby")

    results = resolver.resolve_many([bad, good])
    assert results[0].status == STATUS_UNRESOLVED
    assert "kaboom" in results[0].reason
    assert results[1].status == STATUS_RESOLVED
    assert results[1].identity_key == "psp:ULJM05800"


def test_resolver_bind_requires_identity_or_rom():
    resolver = GameIdentityResolver()
    with pytest.raises(ValueError):
        resolver.bind(make_entry())


def test_resolver_bind_with_rom_path(tmp_path: Path):
    rom = tmp_path / "Kirby.gba"
    rom.write_bytes(make_gba_rom())
    resolver = GameIdentityResolver()
    bound = resolver.bind(make_entry(), rom_path=rom)
    assert bound.source == "manual"
    assert bound.rom_sha1
    assert bound.identity_key.startswith("gba:sha1:")


def test_resolver_bind_with_unreadable_rom_path(tmp_path: Path):
    resolver = GameIdentityResolver()
    with pytest.raises(ValueError):
        resolver.bind(make_entry(), rom_path=tmp_path / "missing.gba")


def test_rom_index_add_root_and_refresh(tmp_path: Path):
    rom_dir = tmp_path / "roms"
    rom_dir.mkdir()
    (rom_dir / "One.gba").write_bytes(make_gba_rom())
    index = RomIndex()
    assert index.all_roms("gba") == []
    index.add_root("gba", rom_dir)
    assert len(index.all_roms("gba")) == 1
    assert index.find("gba", "One")
    index.add_root("gba", rom_dir)  # idempotent
    assert len(index._roots["gba"]) == 1


# --- AppState integration ----------------------------------------------------


def test_app_state_reads_rom_dirs_from_config(tmp_path: Path, monkeypatch):
    save_app_config({"gba_rom_dir": str(tmp_path / "gba"), "nds_rom_dir": str(tmp_path / "nds")})
    state = AppState(library_root=tmp_path / "lib")
    assert state.gba_rom_dir == tmp_path / "gba"
    assert state.nds_rom_dir == tmp_path / "nds"


def test_app_state_ignores_blank_rom_dir_config(tmp_path: Path):
    save_app_config({"gba_rom_dir": "   ", "nds_rom_dir": 123})
    state = AppState(library_root=tmp_path / "lib")
    assert state.gba_rom_dir is None
    assert state.nds_rom_dir is None


def test_app_state_set_rom_dirs_persists_and_clears(tmp_path: Path):
    state = AppState(library_root=tmp_path / "lib")
    state.set_rom_dirs(tmp_path / "gba", tmp_path / "nds")
    assert state.gba_rom_dir == tmp_path / "gba"
    assert state.nds_rom_dir == tmp_path / "nds"
    assert load_app_config()["gba_rom_dir"] == str(tmp_path / "gba")

    # Sentinel keeps the untouched platform while clearing the other.
    state.set_rom_dirs(gba_rom_dir=None)
    assert state.gba_rom_dir is None
    assert state.nds_rom_dir == tmp_path / "nds"
    assert "gba_rom_dir" not in load_app_config()
    assert load_app_config()["nds_rom_dir"] == str(tmp_path / "nds")


def test_app_state_resolve_and_persist_binding(tmp_path: Path):
    rom_dir = tmp_path / "roms"
    rom_dir.mkdir()
    (rom_dir / "Kirby.gba").write_bytes(make_gba_rom())
    lib = tmp_path / "lib"
    state = AppState(library_root=lib)
    state.set_rom_dirs(rom_dir, None)

    entry = make_entry(path=tmp_path / "SAVER" / "Kirby.sav")
    first = state.resolve_save_identity(entry)
    assert first.is_resolved
    assert first.identity_key.startswith("gba:sha1:")
    assert (lib / BINDINGS_NAME).is_file()

    # Drop the ROM config: the persisted binding still resolves the save.
    state.set_rom_dirs(None, None)
    second = state.resolve_save_identity(entry)
    assert second.is_resolved
    assert second.identity_key == first.identity_key

    # resolve_identities() with no explicit list uses the scanned saves.
    assert state.resolve_identities([]) == []


def test_app_state_bind_save_identity(tmp_path: Path):
    state = AppState(library_root=tmp_path / "lib")
    entry = make_entry(name="Kirby")
    identity = GameIdentity(identity_key="gba:sha1:zz", platform="gba", title="Kirby")
    bound = state.bind_save_identity(entry, identity=identity)
    assert bound.source == "manual"
    assert state.resolve_save_identity(entry).identity_key == "gba:sha1:zz"


def test_app_state_set_library_root_resets_resolver(tmp_path: Path):
    state = AppState(library_root=tmp_path / "lib-a")
    first = state.identity_resolver
    state.set_library_root(tmp_path / "lib-b")
    assert state.identity_resolver is not first


def test_resolve_identities_uses_scanned_saves(tmp_path: Path, psp_sfo_bytes: bytes):
    root = tmp_path / "PSP_VOL"
    save_dir = root / "PSP" / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    state = AppState(library_root=tmp_path / "lib")
    state.select_mount(root)
    results = state.resolve_identities()
    assert len(results) == 1
    assert results[0].identity_key == "psp:ULJM05800"


def test_settings_dialog_apply_rom_dirs(tmp_path: Path):
    """The settings dialog's apply hook persists both ROM directories."""
    from vajsave.app_ui import VajSaveApp

    state = AppState(library_root=tmp_path / "lib")

    class Stub:
        def __init__(self) -> None:
            self.state = state
            self.status = None

        def update_status(self, text: str) -> None:
            self.status = text

    stub = Stub()
    VajSaveApp._apply_rom_dirs(stub, tmp_path / "gba", None)
    assert state.gba_rom_dir == tmp_path / "gba"
    assert state.nds_rom_dir is None
    assert stub.status == "ROM 目录已更新"


def test_package_exports_identity_surface():
    import vajsave

    for name in (
        "GameIdentity",
        "GameIdentityResult",
        "GameIdentityResolver",
        "BindingStore",
        "RomIndex",
    ):
        assert name in vajsave.__all__
        assert hasattr(vajsave, name)
    assert vajsave.GameIdentity is GameIdentity

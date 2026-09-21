"""Per-platform identity resolver tests for PSP / Vita / 3DS / Switch.

These platforms carry metadata instead of a matchable ROM: PSP reads
``PARAM.SFO``, the others reuse the scanned ``title_id``.  Missing or corrupt
metadata must degrade gracefully, never raise.
"""

from pathlib import Path

from conftest import build_sfo
from vajsave.identity import GameIdentityResolver
from vajsave.models import SaveEntry


def entry(platform, name, path, title_id=None, **kwargs):
    return SaveEntry(
        platform=platform,
        source_id=f"{platform}_test",
        display_name=name,
        path=str(path),
        title_id=title_id,
        **kwargs,
    )


# --- PSP ---------------------------------------------------------------------


def test_psp_reads_title_id_from_param_sfo(tmp_path: Path):
    save_dir = tmp_path / "PSP" / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(
        build_sfo({"TITLE": "Monster Hunter", "TITLE_ID": "ULJM05800"})
    )
    resolver = GameIdentityResolver()
    result = resolver.resolve(entry("psp", "ULJM05800", save_dir))
    assert result.is_resolved
    assert result.identity.identity_key == "psp:ULJM05800"
    assert result.identity.title == "Monster Hunter"
    assert result.identity.source == "sfo"


def test_psp_savedata_directory_fallback(tmp_path: Path):
    save_dir = tmp_path / "PSP" / "SAVEDATA" / "ULUS12345"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(
        build_sfo({"SAVEDATA_DIRECTORY": "ULUS12345", "TITLE": "Other"})
    )
    result = GameIdentityResolver().resolve(entry("psp", "Other", save_dir))
    assert result.identity_key == "psp:ULUS12345"


def test_psp_corrupt_sfo_does_not_raise(tmp_path: Path):
    save_dir = tmp_path / "PSP" / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(b"not a real sfo")

    result = GameIdentityResolver().resolve(entry("psp", "Monster Hunter", save_dir))
    assert result.status == "partial"
    assert result.identity_key == "psp:name:monster hunter"


def test_psp_missing_sfo_uses_scanned_title_id(tmp_path: Path):
    save_dir = tmp_path / "SAVEDATA" / "ULJM05800"
    save_dir.mkdir(parents=True)
    result = GameIdentityResolver().resolve(
        entry("psp", "Monster Hunter", save_dir, title_id="ULJM05800")
    )
    assert result.is_resolved
    assert result.identity.identity_key == "psp:ULJM05800"
    assert result.identity.source == "metadata"


def test_psp_no_metadata_is_unresolved(tmp_path: Path):
    save_dir = tmp_path / "SAVEDATA" / "unknown"
    save_dir.mkdir(parents=True)
    result = GameIdentityResolver().resolve(entry("psp", "", save_dir))
    assert result.status == "unresolved"


# --- Vita --------------------------------------------------------------------


def test_vita_native_reads_sce_sys_sfo(tmp_path: Path, vita_sfo_bytes: bytes):
    save_dir = tmp_path / "user" / "00" / "savedata" / "PCSE00120"
    (save_dir / "sce_sys").mkdir(parents=True)
    (save_dir / "sce_sys" / "param.sfo").write_bytes(vita_sfo_bytes)

    result = GameIdentityResolver().resolve(entry("vita", "PCSE00120", save_dir))
    assert result.is_resolved
    assert result.identity_key == "vita:PCSE00120"
    assert result.identity.title == "Persona 4 Golden"


def test_vita_exported_reuses_scanned_title_id(tmp_path: Path):
    save_dir = tmp_path / "data" / "savegames" / "PCSE00120"
    save_dir.mkdir(parents=True)
    result = GameIdentityResolver().resolve(
        entry("vita", "PCSE00120", save_dir, title_id="PCSE00120")
    )
    assert result.identity_key == "vita:PCSE00120"


def test_vita_missing_metadata_is_partial(tmp_path: Path):
    save_dir = tmp_path / "savegames" / "Some Game"
    save_dir.mkdir(parents=True)
    result = GameIdentityResolver().resolve(entry("vita", "Some Game", save_dir))
    assert result.status == "partial"
    assert result.identity_key == "vita:name:some game"


def test_vita_corrupt_sfo_does_not_raise(tmp_path: Path):
    save_dir = tmp_path / "savegames" / "PCSE00120"
    (save_dir / "sce_sys").mkdir(parents=True)
    (save_dir / "sce_sys" / "param.sfo").write_bytes(b"garbage")
    result = GameIdentityResolver().resolve(
        entry("vita", "Game", save_dir, title_id="PCSE00120")
    )
    assert result.is_resolved
    assert result.identity_key == "vita:PCSE00120"


def test_vita_unresolved_without_any_metadata(tmp_path: Path):
    save_dir = tmp_path / "empty"
    save_dir.mkdir()
    result = GameIdentityResolver().resolve(entry("vita", "", save_dir))
    assert result.status == "unresolved"


# --- 3DS ---------------------------------------------------------------------


def test_threeds_reuses_scanned_title_id(tmp_path: Path):
    save_dir = tmp_path / "3ds" / "Checkpoint" / "saves" / "0x011C4 Pokemon Moon" / "slot0"
    save_dir.mkdir(parents=True)
    result = GameIdentityResolver().resolve(
        entry("3ds", "Pokemon Moon", save_dir, title_id="0x011C4")
    )
    assert result.is_resolved
    assert result.identity_key == "3ds:0x011C4"
    assert result.identity.title == "Pokemon Moon"


def test_threeds_missing_title_id_is_partial(tmp_path: Path):
    save_dir = tmp_path / "JKSV" / "Saves" / "Pokemon Moon"
    save_dir.mkdir(parents=True)
    result = GameIdentityResolver().resolve(entry("3ds", "Pokemon Moon", save_dir))
    assert result.status == "partial"
    assert result.identity_key == "3ds:name:pokemon moon"


def test_threeds_unresolved_without_metadata(tmp_path: Path):
    save_dir = tmp_path / "slot0"
    save_dir.mkdir()
    result = GameIdentityResolver().resolve(entry("3ds", "", save_dir))
    assert result.status == "unresolved"


# --- Switch ------------------------------------------------------------------


def test_switch_reuses_scanned_title_id(tmp_path: Path):
    save_dir = tmp_path / "switch" / "Checkpoint" / "saves" / "0100000000010000 Mario" / "slot0"
    save_dir.mkdir(parents=True)
    result = GameIdentityResolver().resolve(
        entry("switch", "Mario", save_dir, title_id="0100000000010000")
    )
    assert result.is_resolved
    assert result.identity_key == "switch:0100000000010000"


def test_switch_missing_title_id_is_partial(tmp_path: Path):
    save_dir = tmp_path / "JKSV" / "Zelda"
    save_dir.mkdir(parents=True)
    result = GameIdentityResolver().resolve(entry("switch", "Zelda", save_dir))
    assert result.status == "partial"
    assert result.identity_key == "switch:name:zelda"


def test_switch_unresolved_without_metadata(tmp_path: Path):
    save_dir = tmp_path / "empty"
    save_dir.mkdir()
    result = GameIdentityResolver().resolve(entry("switch", "", save_dir))
    assert result.status == "unresolved"


# --- cross-platform dispatch -------------------------------------------------


def test_single_resolver_dispatches_every_platform(tmp_path: Path, psp_sfo_bytes: bytes):
    resolver = GameIdentityResolver()
    psp_dir = tmp_path / "psp"
    psp_dir.mkdir()
    (psp_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)

    entries = [
        entry("psp", "MH", psp_dir),
        entry("vita", "PCSE00120", tmp_path, title_id="PCSE00120"),
        entry("3ds", "Moon", tmp_path, title_id="0x011C4"),
        entry("switch", "Mario", tmp_path, title_id="0100000000010000"),
    ]
    results = resolver.resolve_many(entries)
    assert [r.identity_key for r in results] == [
        "psp:ULJM05800",
        "vita:PCSE00120",
        "3ds:0x011C4",
        "switch:0100000000010000",
    ]


def test_vita_and_psp_skip_sfo_and_iterdir_when_title_id_present(
    monkeypatch, tmp_path: Path, psp_sfo_bytes: bytes, vita_sfo_bytes: bytes
):
    resolver = GameIdentityResolver()

    # PSP
    psp_save = tmp_path / "PSP" / "SAVEDATA" / "ULJM05800"
    psp_save.mkdir(parents=True)
    (psp_save / "PARAM.SFO").write_bytes(psp_sfo_bytes)

    original_iterdir = Path.iterdir
    iterdir_called = []

    def tracked_iterdir(self):
        iterdir_called.append(self.resolve())
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", tracked_iterdir)

    psp_entry = entry("psp", "Monster Hunter", psp_save, title_id="ULJM05800")
    psp_result = resolver.resolve(psp_entry)
    assert psp_result.is_resolved
    assert psp_result.identity.identity_key == "psp:ULJM05800"
    assert psp_result.identity.source == "metadata"
    assert psp_save.resolve() not in iterdir_called

    # Vita
    vita_save = tmp_path / "user" / "00" / "savedata" / "PCSE00120"
    vita_sce = vita_save / "sce_sys"
    vita_sce.mkdir(parents=True)
    (vita_sce / "param.sfo").write_bytes(vita_sfo_bytes)

    from vajsave import sfo
    original_parse = sfo.parse_sfo
    parsed_sfos = []

    def tracked_parse(path):
        parsed_sfos.append(path)
        return original_parse(path)

    monkeypatch.setattr("vajsave.identity.vita.parse_sfo", tracked_parse)

    vita_entry = entry("vita", "Persona 4 Golden", vita_save, title_id="PCSE00120")
    vita_result = resolver.resolve(vita_entry)
    assert vita_result.is_resolved
    assert vita_result.identity.identity_key == "vita:PCSE00120"
    assert vita_result.identity.source == "metadata"
    assert not parsed_sfos


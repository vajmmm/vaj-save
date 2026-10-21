"""Tests for game grouping across all platforms."""

from pathlib import Path
from vajsave.app_state import AppState
from vajsave.game_grouping import format_sub_entry_label, game_identity_key_for_entry, group_saves_by_game
from vajsave.models import SaveEntry, ScanResult
from vajsave.library import backup_save, load_catalog


def test_switch_saves_grouped_across_sources():
    app = AppState()
    # JKSV export and DBI MTP save for Super Mario 3D All-Stars
    e1 = SaveEntry(
        platform="switch",
        source_id="switch_jksv",
        display_name="Super Mario 3D All-Stars",
        title_id="010049900F556000",
        path="/sd/JKSV/010049900F556000",
    )
    e2 = SaveEntry(
        platform="switch",
        source_id="switch_dbi",
        display_name="Super Mario 3D All-Stars",
        title_id=None,
        user="vajmm",
        path="/sd/Installed games/Super Mario 3D All-Stars/vajmm",
    )
    # Checkpoint and DBI for Super Smash Bros Ultimate
    e3 = SaveEntry(
        platform="switch",
        source_id="switch_checkpoint",
        display_name="Super Smash Bros. Ultimate",
        title_id="01006A800016E000",
        slot="2021.10.23 save_data",
        path="/sd/switch/Checkpoint/saves/0x01006A800016E000/2021.10.23 save_data",
    )
    e4 = SaveEntry(
        platform="switch",
        source_id="switch_dbi",
        display_name="Super Smash Bros. Ultimate",
        title_id=None,
        user="vajmm",
        path="/sd/Uninstalled games/Super Smash Bros. Ultimate/vajmm",
    )

    grouped = group_saves_by_game([e1, e2, e3, e4], app)
    assert len(grouped) == 2

    mario = next(g for g in grouped if "Mario" in g.display_name)
    assert len(mario.extra["sub_entries"]) == 2
    assert mario.title_id == "010049900F556000"

    smash = next(g for g in grouped if "Smash" in g.display_name)
    assert len(smash.extra["sub_entries"]) == 2
    assert smash.title_id == "01006A800016E000"


def test_switch_multi_user_grouped():
    app = AppState()
    e1 = SaveEntry(
        platform="switch",
        source_id="switch_dbi",
        display_name="Kirby and the Forgotten Land",
        title_id="010000500D0FC000",
        user="user1",
        path="/sd/Installed games/Kirby/user1",
    )
    e2 = SaveEntry(
        platform="switch",
        source_id="switch_dbi",
        display_name="Kirby and the Forgotten Land",
        title_id="010000500D0FC000",
        user="user2",
        path="/sd/Installed games/Kirby/user2",
    )

    grouped = group_saves_by_game([e1, e2], app)
    assert len(grouped) == 1
    assert len(grouped[0].extra["sub_entries"]) == 2
    labels = [format_sub_entry_label(s) for s in grouped[0].extra["sub_entries"]]
    assert any("user1" in lbl for lbl in labels)
    assert any("user2" in lbl for lbl in labels)


def test_vita_grouping_does_not_reread_param_sfo(tmp_path: Path, monkeypatch):
    """A gallery refresh must not open sce_sys while the card is being hashed."""
    save = tmp_path / "user" / "00" / "savedata" / "PCSE00001"
    (save / "sce_sys").mkdir(parents=True)
    (save / "sce_sys" / "param.sfo").write_bytes(b"unread")
    entry = SaveEntry(
        platform="vita",
        source_id="vita",
        display_name="PCSE00001",
        title_id="PCSE00001",
        path=str(save),
    )

    def boom(_directory):
        raise AssertionError("grouping re-read Vita param.sfo")

    monkeypatch.setattr("vajsave.identity.vita._read_param_sfo", boom)
    app = AppState()
    app.current_result = ScanResult(root_path=str(tmp_path), platform="vita", saves=[entry])
    grouped = app.visible_saves()
    assert len(grouped) == 1
    assert grouped[0].title_id == "PCSE00001"


def test_vita_cover_lookup_does_not_reread_param_sfo(tmp_path: Path, monkeypatch):
    """Painting a case must not open the save while verification owns the card."""
    save = tmp_path / "user" / "00" / "savedata" / "PCSE00001"
    (save / "sce_sys").mkdir(parents=True)
    (save / "sce_sys" / "param.sfo").write_bytes(b"unread")
    (save / "data.bin").write_bytes(b"save")
    entry = SaveEntry(
        platform="vita",
        source_id="vita",
        display_name="PCSE00001",
        title_id="PCSE00001",
        path=str(save),
    )

    def boom(_directory):
        raise AssertionError("cover lookup re-read Vita param.sfo")

    monkeypatch.setattr("vajsave.identity.vita._read_param_sfo", boom)
    listed: list[str] = []
    original_iterdir = Path.iterdir

    def tracking_iterdir(self, *args, **kwargs):
        if self == save or save in self.parents:
            listed.append(str(self))
        return original_iterdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "iterdir", tracking_iterdir)
    app = AppState(library_root=tmp_path / "lib")
    app.resolve_save_cover(entry)
    assert listed == []


def test_multi_slot_grouping_does_not_stat_the_card(tmp_path: Path):
    """Choosing a representative slot must not stat saves on the UI thread."""
    first = tmp_path / "SAVEDATA" / "SLOT0"
    second = tmp_path / "SAVEDATA" / "SLOT1"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "data.bin").write_bytes(b"a")
    (second / "data.bin").write_bytes(b"b")
    lib = tmp_path / "lib"
    older = SaveEntry("psp", "psp", "Monster Hunter", str(first), "ULJM05800", slot="0")
    newer = SaveEntry("psp", "psp", "Monster Hunter", str(second), "ULJM05800", slot="1")
    backup_save(older, lib)
    backup_save(newer, lib)
    app = AppState(library_root=lib)
    touched: list[str] = []
    original_stat = Path.stat

    def tracking_stat(self, *args, **kwargs):
        if self == first or self == second or first in self.parents or second in self.parents:
            touched.append(str(self))
        return original_stat(self, *args, **kwargs)

    Path.stat = tracking_stat
    try:
        grouped = group_saves_by_game([older, newer], app)
    finally:
        Path.stat = original_stat
    assert len(grouped) == 1
    assert touched == []


def test_psp_multi_slot_grouped(tmp_path: Path, psp_sfo_bytes: bytes):
    save1 = tmp_path / "PSP" / "SAVEDATA" / "ULJM05800000"
    save1.mkdir(parents=True)
    (save1 / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    (save1 / "DATA.BIN").write_bytes(b"slot0")

    save2 = tmp_path / "PSP" / "SAVEDATA" / "ULJM05800001"
    save2.mkdir(parents=True)
    (save2 / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    (save2 / "DATA.BIN").write_bytes(b"slot1")

    app = AppState()
    e1 = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="Monster Hunter Portable 3rd",
        title_id="ULJM05800000",
        path=str(save1),
    )
    e2 = SaveEntry(
        platform="psp",
        source_id="psp",
        display_name="Monster Hunter Portable 3rd",
        title_id="ULJM05800001",
        path=str(save2),
    )

    grouped = group_saves_by_game([e1, e2], app)
    assert len(grouped) == 1
    assert len(grouped[0].extra["sub_entries"]) == 2


def test_3ds_multi_slot_grouped():
    app = AppState()
    e1 = SaveEntry(
        platform="3ds",
        source_id="3ds_checkpoint",
        display_name="New Super Mario Bros 2",
        title_id="0x0137E",
        slot="slot1",
        path="/sd/3ds/Checkpoint/saves/0x0137E/slot1",
    )
    e2 = SaveEntry(
        platform="3ds",
        source_id="3ds_checkpoint",
        display_name="New Super Mario Bros 2",
        title_id="0x0137E",
        slot="slot2",
        path="/sd/3ds/Checkpoint/saves/0x0137E/slot2",
    )

    grouped = group_saves_by_game([e1, e2], app)
    assert len(grouped) == 1
    assert len(grouped[0].extra["sub_entries"]) == 2


def test_status_aggregated_when_one_entry_changed(tmp_path: Path):
    lib = tmp_path / "lib"
    s1 = tmp_path / "saves" / "user1"
    s1.mkdir(parents=True)
    (s1 / "save.bin").write_bytes(b"v1")

    s2 = tmp_path / "saves" / "user2"
    s2.mkdir(parents=True)
    (s2 / "save.bin").write_bytes(b"v1")

    e1 = SaveEntry(
        platform="switch",
        source_id="switch_dbi",
        display_name="Game X",
        title_id="0100111122223333",
        user="user1",
        path=str(s1),
    )
    e2 = SaveEntry(
        platform="switch",
        source_id="switch_dbi",
        display_name="Game X",
        title_id="0100111122223333",
        user="user2",
        path=str(s2),
    )

    # Backup both so they start unchanged
    backup_save(e1, lib)
    backup_save(e2, lib)

    app = AppState(library_root=lib)
    app.current_result = ScanResult(root_path=str(tmp_path), platform="switch", saves=[e1, e2])

    grouped = app.visible_saves()
    assert len(grouped) == 1
    assert app.save_status(grouped[0]).status == "unchanged"

    # Now modify user2 only
    (s2 / "save.bin").write_bytes(b"v2-new-content")
    app._backup_statuses.clear()

    # The game's combined status should be 'changed'
    assert app.save_status(grouped[0]).status == "changed"

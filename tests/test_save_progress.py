"""Progress text taken from the console's own save subtitle."""

from pathlib import Path

from conftest import build_sfo
from vajsave.game_grouping import format_sub_entry_label
from vajsave.models import SaveEntry
from vajsave.save_progress import progress_from_sfo, progress_text
from vajsave.scanner import scan


def test_sfo_subtitle_is_progress_and_title_is_not():
    assert progress_from_sfo(
        {"TITLE": "Monster Hunter", "SUB_TITLE": "村任务 upper 12:40", "TITLE_ID": "ULJM05800"}
    ) == "村任务 upper 12:40"
    assert progress_from_sfo({"TITLE": "Monster Hunter", "SUB_TITLE": "Monster Hunter"}) == ""
    assert progress_from_sfo({"TITLE": "Game", "DETAIL": "第3章 城下町"}) == "第3章 城下町"
    assert progress_from_sfo({}) == ""


def test_psp_scan_keeps_subtitle_for_each_slot(tmp_path: Path):
    root = tmp_path / "card"
    first = root / "PSP" / "SAVEDATA" / "ULJM05800000"
    second = root / "PSP" / "SAVEDATA" / "ULJM05800001"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "PARAM.SFO").write_bytes(
        build_sfo({"TITLE": "Monster Hunter", "TITLE_ID": "ULJM05800", "SUB_TITLE": "村 上位"})
    )
    (second / "PARAM.SFO").write_bytes(
        build_sfo({"TITLE": "Monster Hunter", "TITLE_ID": "ULJM05800", "DETAIL": "集会所 HR 6"})
    )

    saves = {Path(save.path).name: save for save in scan(root).saves}
    assert progress_text(saves["ULJM05800000"]) == "村 上位"
    assert progress_text(saves["ULJM05800001"]) == "集会所 HR 6"
    label = format_sub_entry_label(saves["ULJM05800000"])
    assert "村 上位" in label


def test_vita_scan_reads_param_sfo_subtitle(tmp_path: Path):
    save = tmp_path / "ux0" / "user" / "00" / "savedata" / "PCSE00001"
    sce = save / "sce_sys"
    sce.mkdir(parents=True)
    (sce / "param.sfo").write_bytes(
        build_sfo({"TITLE": "Persona 4", "TITLE_ID": "PCSE00001", "SUB_TITLE": "6/12 午后"})
    )
    found = scan(tmp_path).saves
    assert len(found) == 1
    assert progress_text(found[0]) == "6/12 午后"

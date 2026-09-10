from pathlib import Path

from vajsave.app_state import AppState, PLATFORM_ORDER
from vajsave.models import VolumeInfo
from vajsave.volume import FakeVolumeProvider
from conftest import build_sfo


def _vita_psp_volume(tmp_path: Path, vita_sfo_bytes: bytes, psp_sfo_bytes: bytes) -> Path:
    root = tmp_path / "MIXED"
    vita_dir = root / "user" / "00" / "savedata" / "PCSE00120" / "sce_sys"
    vita_dir.mkdir(parents=True)
    (vita_dir / "param.sfo").write_bytes(vita_sfo_bytes)
    psp_dir = root / "pspemu" / "PSP" / "SAVEDATA" / "ULJM05800"
    psp_dir.mkdir(parents=True)
    (psp_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)
    return root


def test_platform_counts_and_filter(tmp_path: Path, vita_sfo_bytes: bytes, psp_sfo_bytes: bytes):
    root = _vita_psp_volume(tmp_path, vita_sfo_bytes, psp_sfo_bytes)
    state = AppState(provider=FakeVolumeProvider([VolumeInfo(name="MIX", mount_point=root)]))
    state.select_mount(root)

    counts = state.platform_counts()
    assert counts["psp"] == 1
    assert counts["vita"] == 1
    assert counts["switch"] == 0
    assert counts["3ds"] == 0
    assert state.selected_platform == "all"
    assert len(state.visible_saves()) == 2

    state.set_platform_filter("psp")
    visible = state.visible_saves()
    assert len(visible) == 1
    assert visible[0].platform == "psp"

    state.set_platform_filter("vita")
    assert [s.platform for s in state.visible_saves()] == ["vita"]

    state.set_platform_filter("all")
    assert len(state.visible_saves()) == 2


def test_grouped_saves_orders_known_platforms(tmp_path: Path, vita_sfo_bytes: bytes, psp_sfo_bytes: bytes):
    root = _vita_psp_volume(tmp_path, vita_sfo_bytes, psp_sfo_bytes)
    state = AppState()
    state.select_mount(root)
    groups = state.grouped_saves()
    platforms = [p for p, _ in groups]
    assert platforms == ["psp", "vita"]
    assert PLATFORM_ORDER.index("psp") < PLATFORM_ORDER.index("vita")


def test_empty_result_has_zero_counts(tmp_path: Path):
    state = AppState()
    assert state.platform_counts()["psp"] == 0
    assert state.visible_saves() == []
    assert state.grouped_saves() == []

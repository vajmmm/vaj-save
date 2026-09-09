from pathlib import Path
from vajsave.models import SaveEntry, SaveSource, ScanResult, VolumeInfo


def test_models_to_dict():
    entry = SaveEntry(
        platform="switch",
        source_id="switch_jksv",
        display_name="Zelda",
        path="/path/to/zelda",
        title_id="0100000000010000",
        slot="Slot1",
        user="Player1",
        extra={"notes": "test"},
    )
    d = entry.to_dict()
    assert d["platform"] == "switch"
    assert d["title_id"] == "0100000000010000"
    assert d["slot"] == "Slot1"
    assert d["user"] == "Player1"
    assert d["extra"]["notes"] == "test"

    source = SaveSource(
        source_id="3ds_sd",
        platform="3ds",
        description="Encrypted 3DS SD",
        root_path="/3ds",
        extra={"encrypted_container": True},
    )
    sd = source.to_dict()
    assert sd["extra"]["encrypted_container"] is True

    vol = VolumeInfo(
        name="USB",
        mount_point=Path("/Volumes/USB"),
        is_removable=True,
        extra={"fs": "FAT32"},
    )
    vd = vol.to_dict()
    assert vd["name"] == "USB"
    assert vd["extra"]["fs"] == "FAT32"

    scan_res = ScanResult(
        root_path="/root",
        platform="3ds",
        sources=[source],
        saves=[entry],
        warnings=["warn1"],
    )
    rd = scan_res.to_dict()
    assert len(rd["sources"]) == 1
    assert len(rd["saves"]) == 1
    assert rd["warnings"] == ["warn1"]

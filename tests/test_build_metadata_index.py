"""The bundled-index generator: real DAT parsing, determinism and provenance.

The app ships a compact index produced by ``tools/build_metadata_index.py``.
These tests pin that the generator understands both DAT dialects (Logiqx XML
and ClrMamePro text), emits a deterministic, sorted, de-duplicated index, and
writes provenance/license metadata alongside it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "build_metadata_index.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("build_metadata_index", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_metadata_index"] = module
    spec.loader.exec_module(module)
    return module


build = _load_tool()

SHA1_A = "a" * 40
SHA1_B = "b" * 40
CRC_A = "1a2b3c4d"
CRC_B = "0000abcd"

XML_DAT = """<?xml version="1.0"?>
<datafile xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <header>
    <name>Nintendo - Game Boy Advance</name>
    <version>20260101-000000</version>
    <clrmamepro forcenodump="required"/>
  </header>
  <game name="Game A (USA)" id="1">
    <description>Game A (USA)</description>
    <rom name="Game A (USA).gba" size="1024" crc="{crc_a}" sha1="{sha1_a}" serial="BPEE"/>
  </game>
  <game name="Game B (Japan)" id="2">
    <description>Game B (Japan)</description>
    <rom name="Game B (Japan).gba" size="2048" crc="{crc_b}" sha1="{sha1_b}" serial="!n/a"/>
  </game>
</datafile>
""".format(crc_a=CRC_A, sha1_a=SHA1_A, crc_b=CRC_B, sha1_b=SHA1_B)

CMP_DAT = """clrmamepro (
	name "Nintendo - Game Boy Advance"
	version "20260101-000000"
)
game (
	name "Game A (USA)"
	description "Game A (USA)"
	rom ( name "Game A (USA).gba" size 1024 crc {crc_a} sha1 {sha1_a} serial BPEE )
)
game (
	name "Game B (Japan)"
	description "Game B (Japan)"
	rom ( name "Game B (Japan).gba" size 2048 crc {crc_b} sha1 {sha1_b} serial "!n/a" )
)
""".format(crc_a=CRC_A, sha1_a=SHA1_A, crc_b=CRC_B, sha1_b=SHA1_B)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_xml_and_clrmamepro_dialects_agree(tmp_path: Path):
    xml = _write(tmp_path, "xml.dat", XML_DAT)
    cmp_ = _write(tmp_path, "cmp.dat", CMP_DAT)
    xml_index = build.build_index([xml], platform="gba")
    cmp_index = build.build_index([cmp_], platform="gba")
    # ClrMamePro text has no No-Intro ``id``; the hash/title/serial payload
    # parsed from both dialects must otherwise be identical.
    assert [row[:4] for row in xml_index["records"]] == [
        row[:4] for row in cmp_index["records"]
    ]
    assert [(row[0], row[4]) for row in xml_index["records"]] == [
        (SHA1_A, "1"),
        (SHA1_B, "2"),
    ]
    assert xml_index["records"][0][:4] == [SHA1_A, CRC_A, "Game A (USA)", "BPEE"]


def test_index_is_deterministic_and_sorted(tmp_path: Path):
    dat = _write(tmp_path, "gba.dat", XML_DAT)
    first = build.build_index([dat], platform="gba")
    second = build.build_index([dat], platform="gba")
    assert json.dumps(first, ensure_ascii=False) == json.dumps(second, ensure_ascii=False)
    assert first["records"] == sorted(first["records"], key=lambda r: (r[0], r[1], r[2]))
    assert first["format"] == build.FORMAT_NAME
    assert first["platform"] == "gba"
    assert first["sources"][0]["version"] == "20260101-000000"


def test_platform_detection_and_mismatch(tmp_path: Path):
    dat = _write(tmp_path, "gba.dat", XML_DAT)
    index = build.build_index([dat])
    assert index["platform"] == "gba"
    with pytest.raises(build.DatParseError):
        build.build_index([dat], platform="nds")
    with pytest.raises(build.DatParseError):
        build.build_index([dat], platform="psp")


def test_parse_dat_rejects_garbage(tmp_path: Path):
    garbage = _write(tmp_path, "x.dat", "this is not a dat\n")
    with pytest.raises(build.DatParseError):
        build.parse_dat(garbage)


def test_normalize_hex_and_detect_platform():
    assert build.normalize_hex("A1B2", 8) == "0000a1b2"
    assert build.normalize_hex("0xA1B2", 8) == "0000a1b2"
    assert build.normalize_hex("zz", 8) == ""
    assert build.detect_platform("Nintendo - Nintendo DS") == "nds"


def test_cli_writes_index_and_provenance(tmp_path: Path):
    dat = _write(tmp_path, "gba.dat", XML_DAT)
    out = tmp_path / "out" / "gba.json"
    prov = tmp_path / "out" / "provenance.json"
    rc = build.main(
        ["--platform", "gba", "--dat", str(dat), "--out", str(out), "--provenance", str(prov)]
    )
    assert rc == 0
    index = json.loads(out.read_text(encoding="utf-8"))
    assert index["format"] == build.FORMAT_NAME
    assert len(index["records"]) == 2
    provenance = json.loads(prov.read_text(encoding="utf-8"))
    assert provenance["indexes"][0]["platform"] == "gba"
    assert "No-Intro" in provenance["license"]["name"]


def test_cli_requires_input(tmp_path: Path):
    with pytest.raises(SystemExit):
        build.main(["--platform", "gba", "--out", str(tmp_path / "x.json")])


def test_bundled_index_matches_provenance():
    provenance = json.loads(
        (ROOT / "src" / "vajsave" / "data" / "libretro" / "provenance.json").read_text(
            encoding="utf-8"
        )
    )
    platforms = {entry["platform"] for entry in provenance["indexes"]}
    assert platforms == {"gba", "nds"}
    assert provenance["format"] == build.PROVENANCE_FORMAT_NAME
    for entry in provenance["indexes"]:
        assert entry["sources"], entry["platform"]
        bundled = ROOT / "src" / "vajsave" / "data" / "libretro" / f"{entry['platform']}.json"
        index = json.loads(bundled.read_text(encoding="utf-8"))
        assert index["format"] == build.FORMAT_NAME
        assert index["platform"] == entry["platform"]


def test_bundled_index_contains_real_known_hashes():
    gba = json.loads(
        (ROOT / "src" / "vajsave" / "data" / "libretro" / "gba.json").read_text(encoding="utf-8")
    )
    nds = json.loads(
        (ROOT / "src" / "vajsave" / "data" / "libretro" / "nds.json").read_text(encoding="utf-8")
    )
    assert any(
        row[0] == "f3ae088181bf583e55daf962a92bb46f4f1d07b7"
        and row[2] == "Pokemon - Emerald Version (USA, Europe)"
        for row in gba["records"]
    )
    assert any(
        row[0] == "007d061e1abc8d9b56c6378c82fcfb3fc990adf3"
        and row[2] == "Pokemon - HeartGold Version (USA)"
        for row in nds["records"]
    )

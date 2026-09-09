from pathlib import Path
import pytest
from vajsave.sfo import parse_sfo
from conftest import build_sfo


def test_parse_valid_sfo(tmp_path: Path, psp_sfo_bytes: bytes):
    sfo_file = tmp_path / "PARAM.SFO"
    sfo_file.write_bytes(psp_sfo_bytes)

    # Test Path object
    result = parse_sfo(sfo_file)
    assert result.get("TITLE") == "Monster Hunter Portable 3rd"
    assert result.get("TITLE_ID") == "ULJM05800"
    assert result.get("CATEGORY") == "MS"
    assert result.get("SAVEDATA_DIRECTORY") == "ULJM05800"

    # Test string path
    result_str = parse_sfo(str(sfo_file))
    assert result_str.get("TITLE") == "Monster Hunter Portable 3rd"


def test_parse_sfo_from_bytes(vita_sfo_bytes: bytes):
    result = parse_sfo(vita_sfo_bytes)
    assert result.get("TITLE") == "Persona 4 Golden"
    assert result.get("TITLE_ID") == "PCSE00120"
    assert result.get("CATEGORY") == "gd"


def test_parse_sfo_integer_values():
    sfo_bytes = build_sfo({
        "TITLE": "Test Game",
        "PARENTAL_LEVEL": 5,
    })
    result = parse_sfo(sfo_bytes)
    assert result.get("TITLE") == "Test Game"
    assert result.get("PARENTAL_LEVEL") == 5


def test_parse_empty_or_truncated_sfo(tmp_path: Path):
    empty_file = tmp_path / "EMPTY.SFO"
    empty_file.write_bytes(b"")
    assert parse_sfo(empty_file) == {}

    short_file = tmp_path / "SHORT.SFO"
    short_file.write_bytes(b"\x00PSF\x01\x00")
    assert parse_sfo(short_file) == {}


def test_parse_invalid_magic():
    bad_magic = b"INVALID_HEADER_DATA_12345678901234567890"
    assert parse_sfo(bad_magic) == {}


def test_parse_corrupt_offset():
    # Valid magic but crazy table offsets
    bad_offset = b"\x00PSF\x01\x01\x00\x00\xff\xff\x00\x00\xff\xff\x00\x00\x01\x00\x00\x00" + b"\x00" * 32
    assert parse_sfo(bad_offset) == {}


def test_parse_corrupt_index_table_length():
    # Large number of entries exceeding data length
    bad_entries = b"\x00PSF\x01\x01\x00\x00\x20\x00\x00\x00\x30\x00\x00\x00\xff\x00\x00\x00" + b"\x00" * 20
    assert parse_sfo(bad_entries) == {}


def test_parse_nonexistent_file(tmp_path: Path):
    nonexistent = tmp_path / "DOES_NOT_EXIST.SFO"
    assert parse_sfo(nonexistent) == {}


def test_parse_directory_as_sfo(tmp_path: Path):
    sfo_dir = tmp_path / "DIR.SFO"
    sfo_dir.mkdir()
    assert parse_sfo(sfo_dir) == {}

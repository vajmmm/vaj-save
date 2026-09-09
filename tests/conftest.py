import struct
import pytest
from pathlib import Path
from typing import Any, Dict


def build_sfo(entries: Dict[str, Any]) -> bytes:
    """Helper to generate standard binary PARAM.SFO (PSF) bytes."""
    sorted_items = sorted(entries.items(), key=lambda x: x[0])
    num_entries = len(sorted_items)

    key_table = bytearray()
    data_table = bytearray()
    index_entries = []

    for key, val in sorted_items:
        key_offset = len(key_table)
        key_table.extend(key.encode("utf-8") + b"\x00")

        data_offset = len(data_table)
        if isinstance(val, int):
            data_fmt = 0x0404
            data_bytes = struct.pack("<I", val)
            data_len = 4
            data_max_len = 4
        else:
            data_fmt = 0x0204
            raw_bytes = str(val).encode("utf-8") + b"\x00"
            data_len = len(raw_bytes)
            data_max_len = (data_len + 3) & ~3
            data_bytes = raw_bytes.ljust(data_max_len, b"\x00")

        data_table.extend(data_bytes)
        index_entries.append((key_offset, data_fmt, data_len, data_max_len, data_offset))

    header_len = 20
    index_table_len = num_entries * 16
    key_table_offset = header_len + index_table_len

    padding = (4 - (len(key_table) % 4)) % 4
    key_table.extend(b"\x00" * padding)
    data_table_offset = key_table_offset + len(key_table)

    header = struct.pack(
        "<4sIIII",
        b"\x00PSF",
        0x00000101,
        key_table_offset,
        data_table_offset,
        num_entries,
    )

    index_bytes = bytearray()
    for k_off, fmt, d_len, d_max, d_off in index_entries:
        index_bytes.extend(struct.pack("<HHIII", k_off, fmt, d_len, d_max, d_off))

    return bytes(header + index_bytes + key_table + data_table)


@pytest.fixture
def psp_sfo_bytes() -> bytes:
    return build_sfo({
        "TITLE": "Monster Hunter Portable 3rd",
        "TITLE_ID": "ULJM05800",
        "CATEGORY": "MS",
        "SAVEDATA_DIRECTORY": "ULJM05800",
    })


@pytest.fixture
def vita_sfo_bytes() -> bytes:
    return build_sfo({
        "TITLE": "Persona 4 Golden",
        "TITLE_ID": "PCSE00120",
        "CATEGORY": "gd",
    })

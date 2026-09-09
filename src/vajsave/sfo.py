import struct
from pathlib import Path
from typing import Any, Dict, Union


def parse_sfo(source: Union[bytes, Path, str]) -> Dict[str, Any]:
    """Parse a PlayStation PARAM.SFO (PSF) file or bytes.

    Returns a dictionary of key-value pairs (e.g. TITLE, TITLE_ID).
    Returns an empty dict if the file is invalid, missing, or corrupted.
    Never raises an unhandled exception to callers.
    """
    try:
        if isinstance(source, (Path, str)):
            path = Path(source)
            if not path.is_file():
                return {}
            data = path.read_bytes()
        else:
            data = bytes(source)

        if len(data) < 20:
            return {}

        magic, version, key_table_offset, data_table_offset, num_entries = struct.unpack(
            "<4sIIII", data[:20]
        )

        if magic != b"\x00PSF":
            return {}

        if key_table_offset > len(data) or data_table_offset > len(data):
            return {}

        index_table_len = num_entries * 16
        if 20 + index_table_len > len(data):
            return {}

        entries: Dict[str, Any] = {}

        for i in range(num_entries):
            entry_offset = 20 + i * 16
            key_off, data_fmt, data_len, data_max_len, data_off = struct.unpack(
                "<HHIII", data[entry_offset : entry_offset + 16]
            )

            # Read null-terminated key from key table
            abs_key_offset = key_table_offset + key_off
            if abs_key_offset >= len(data):
                continue
            null_pos = data.find(b"\x00", abs_key_offset)
            if null_pos == -1:
                null_pos = len(data)
            try:
                key = data[abs_key_offset:null_pos].decode("utf-8", errors="replace")
            except Exception:
                continue

            # Read value from data table
            abs_data_offset = data_table_offset + data_off
            if abs_data_offset + data_len > len(data):
                continue

            raw_val = data[abs_data_offset : abs_data_offset + data_len]

            if data_fmt in (0x0004, 0x0204):  # UTF-8 string
                val_str = raw_val.rstrip(b"\x00").decode("utf-8", errors="replace")
                entries[key] = val_str
            elif data_fmt == 0x0404:  # uint32 integer
                if len(raw_val) >= 4:
                    val_int = struct.unpack("<I", raw_val[:4])[0]
                    entries[key] = val_int
            else:
                # Unknown format, try string decoding as fallback
                try:
                    entries[key] = raw_val.rstrip(b"\x00").decode("utf-8", errors="replace")
                except Exception:
                    entries[key] = raw_val

        return entries
    except Exception:
        return {}
